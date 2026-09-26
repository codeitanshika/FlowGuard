import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.fraud_client import FraudClient
from app.clients.provider_client import PaymentProvider
from app.clients.user_client import UserClient
from app.db import repository
from app.db.models import IdempotencyKey, Transaction, TransactionStatus
from app.models.schemas import PaymentRequest, PaymentResponse
from shared.circuit_breaker import BreakerOpenError, CircuitBreaker
from shared.errors import ConflictError, DependencyUnavailableError
from shared.events import Channels, RedisEventBus
from shared.idempotency import IdempotencyLock
from shared.logging import get_logger
from shared.telemetry import current_trace_id, inject_context

logger = get_logger(__name__)

IDEMPOTENCY_KEY_TTL = timedelta(hours=24)


def _is_breaker_failure(exc: Exception) -> bool:
    """A business rejection (ConflictError — insufficient balance, high
    fraud risk) is the dependency working correctly and saying no; it
    must never trip a breaker or get retried, only a genuine dependency
    failure should. BreakerOpenError is never passed to this: it's
    raised by the breaker before the wrapped call even runs, so it's
    handled at each call site instead, not here."""
    return isinstance(exc, DependencyUnavailableError)


class PaymentOrchestrator:
    """The transaction state machine. Owns the sequence
    risk-check -> debit -> provider-capture and the compensating action
    (credit back) if capture fails after the debit already succeeded —
    see the Phase 1 write-up on why that compensation exists.

    trace_id is no longer threaded in from the route handler (Phase 1-3
    had it come from an X-Trace-Id header) — Phase 4 derives it directly
    from the active OpenTelemetry span, which is the actual source of
    truth now that FastAPI/httpx auto-instrumentation handles real trace
    propagation on every HTTP hop this service makes or receives.

    Phase 5 wraps every outbound call in its own circuit breaker — three
    independent ones (fraud/user/provider), per docs/architecture/04-lld.md,
    so one dependency degrading can't trip the breaker guarding another."""

    def __init__(
        self,
        fraud_client: FraudClient,
        user_client: UserClient,
        provider: PaymentProvider,
        event_bus: RedisEventBus,
        idempotency_lock: IdempotencyLock,
        fraud_breaker: CircuitBreaker,
        user_breaker: CircuitBreaker,
        provider_breaker: CircuitBreaker,
    ) -> None:
        self._fraud_client = fraud_client
        self._user_client = user_client
        self._provider = provider
        self._event_bus = event_bus
        self._idempotency_lock = idempotency_lock
        self._fraud_breaker = fraud_breaker
        self._user_breaker = user_breaker
        self._provider_breaker = provider_breaker

    async def create_payment(self, db: AsyncSession, payload: PaymentRequest, idempotency_key: str) -> PaymentResponse:
        request_hash = _hash_request(payload)

        existing = await repository.get_idempotency_key(db, idempotency_key)
        if existing is not None:
            return _replay(existing, request_hash, idempotency_key)

        if not await self._idempotency_lock.acquire(idempotency_key):
            raise ConflictError("a request with this idempotency key is already being processed, retry shortly")

        try:
            # Double-checked: another request may have finished and released
            # the lock between our first check above and acquiring it here.
            existing = await repository.get_idempotency_key(db, idempotency_key)
            if existing is not None:
                return _replay(existing, request_hash, idempotency_key)

            return await self._process_payment(db, payload, idempotency_key, request_hash)
        finally:
            await self._idempotency_lock.release(idempotency_key)

    async def _process_payment(
        self,
        db: AsyncSession,
        payload: PaymentRequest,
        idempotency_key: str,
        request_hash: str,
    ) -> PaymentResponse:
        transaction = await repository.create_transaction(
            db,
            user_id=payload.user_id,
            amount=payload.amount,
            currency=payload.currency,
            provider=self._provider.name,
            idempotency_key=idempotency_key,
        )

        await self._event_bus.publish(
            Channels.PAYMENT_CREATED,
            _event_payload(
                Channels.PAYMENT_CREATED,
                transaction_id=str(transaction.id),
                user_id=str(transaction.user_id),
                amount=str(transaction.amount),
                currency=transaction.currency,
            ),
        )

        debited = False
        try:
            await self._run_risk_check(db, transaction)
            await self._debit_user(transaction)
            debited = True
            transaction = await self._capture_with_provider(db, transaction)
        except (ConflictError, DependencyUnavailableError, BreakerOpenError) as exc:
            if not isinstance(exc, ConflictError):
                _mark_span_as_dependency_failure(exc)
            if debited:
                logger.warning("payment.compensating_debit", transaction_id=str(transaction.id))
                await self._credit_user(transaction)
            transaction = await repository.mark_failed(db, transaction, str(exc))
            await self._publish_outcome(transaction)

        response = PaymentResponse.model_validate(transaction)
        await repository.save_idempotency_key(
            db,
            idempotency_key,
            transaction.id,
            request_hash,
            response.model_dump(mode="json"),
            _expiry(),
        )
        return response

    async def _run_risk_check(self, db: AsyncSession, transaction: Transaction) -> None:
        await repository.set_status(db, transaction, TransactionStatus.risk_check)
        try:
            result = await self._fraud_breaker.call(
                self._fraud_client.check_risk,
                transaction.id,
                transaction.user_id,
                transaction.amount,
                transaction.currency,
                is_failure=_is_breaker_failure,
            )
        except (DependencyUnavailableError, BreakerOpenError) as exc:
            # Graceful degradation, not fail-closed: per
            # docs/architecture/07-failure-scenarios.md #2, Fraud Service
            # being unavailable must not block every payment outright —
            # but this is logged loudly (not silently skipped) so it's
            # visible as a degraded-mode transaction, not indistinguishable
            # from a normal low-risk approval.
            logger.warning(
                "payment.fraud_check_degraded",
                transaction_id=str(transaction.id),
                reason=str(exc),
            )
            return
        if result.risk_level == "high":
            raise ConflictError(f"transaction blocked by fraud check: {result.rationale}")

    async def _debit_user(self, transaction: Transaction) -> None:
        # No fallback here, unlike the fraud check — there is no safe
        # "proceed anyway" when the service that would actually move
        # money is unavailable. DependencyUnavailableError/BreakerOpenError
        # propagate to _process_payment's except clause and fail the
        # transaction cleanly.
        await self._user_breaker.call(
            self._user_client.debit,
            transaction.user_id,
            transaction.amount,
            transaction.currency,
            transaction.id,
            is_failure=_is_breaker_failure,
        )

    async def _credit_user(self, transaction: Transaction) -> None:
        # Same breaker as the debit — it's the same dependency. If User
        # Service is unavailable for the compensating credit too, this
        # raises and the transaction is still marked failed below, but
        # the debit is now unreconciled — a known gap (see Phase 1's
        # original note), meaningfully narrowed by the retry+backoff this
        # breaker already applies before giving up, not eliminated.
        await self._user_breaker.call(
            self._user_client.credit,
            transaction.user_id,
            transaction.amount,
            transaction.currency,
            transaction.id,
            is_failure=_is_breaker_failure,
        )

    async def _capture_with_provider(self, db: AsyncSession, transaction: Transaction) -> Transaction:
        transaction = await repository.set_status(db, transaction, TransactionStatus.provider_pending)
        result = await self._provider_breaker.call(
            self._provider.capture,
            transaction.id,
            transaction.amount,
            transaction.currency,
            is_failure=_is_breaker_failure,
        )
        if not result.success:
            raise ConflictError(result.failure_reason or "provider declined the payment")
        transaction = await repository.mark_completed(db, transaction, result.provider_reference)
        await self._publish_outcome(transaction)
        return transaction

    async def _publish_outcome(self, transaction: Transaction) -> None:
        channel = (
            Channels.PAYMENT_COMPLETED if transaction.status == TransactionStatus.completed else Channels.PAYMENT_FAILED
        )
        await self._event_bus.publish(
            channel,
            _event_payload(
                channel,
                transaction_id=str(transaction.id),
                user_id=str(transaction.user_id),
                status=transaction.status.value,
                provider_reference=transaction.provider_reference,
                failure_reason=transaction.failure_reason,
            ),
        )


def _mark_span_as_dependency_failure(exc: Exception) -> None:
    """A payment that fails because a dependency is down is still returned
    as HTTP 201 with status=failed (a recorded outcome, replayable by
    idempotency key), so the request span would otherwise look perfectly
    healthy — and the Monitor Agent, which derives error rate from spans,
    would never see a provider/user outage. Marking the span ERROR here
    keeps the API contract intact while making the failure observable.
    Business rejections (ConflictError) are deliberately not marked: a
    declined or fraud-blocked payment is the system working correctly."""
    span = trace.get_current_span()
    span.set_status(Status(StatusCode.ERROR, str(exc)))
    span.set_attribute("flowguard.failure_kind", "dependency")


def _event_payload(event: str, **fields: Any) -> dict[str, Any]:
    """Every published event gets the same base shape: the event name,
    a human-readable trace_id (handy in logs/API responses/Redis CLI
    inspection without decoding a traceparent), a timestamp, plus a real
    `traceparent` (via inject_context) that Notification's consumer
    extracts to continue this exact trace rather than starting a new
    one — see event_consumer.py."""

    payload = {
        "event": event,
        "trace_id": current_trace_id(),
        "timestamp": _now_iso(),
        **fields,
    }
    return inject_context(payload)


def _replay(existing: IdempotencyKey, request_hash: str, idempotency_key: str) -> PaymentResponse:
    if existing.request_hash != request_hash:
        raise ConflictError("idempotency key already used with a different request body")
    logger.info("payment.idempotent_replay", idempotency_key=idempotency_key)
    return PaymentResponse(**existing.response_snapshot)


def _hash_request(payload: PaymentRequest) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _expiry() -> datetime:
    return datetime.now(UTC) + IDEMPOTENCY_KEY_TTL


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
