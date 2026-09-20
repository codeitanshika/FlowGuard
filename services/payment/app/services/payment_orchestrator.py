import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.fraud_client import FraudClient
from app.clients.provider_client import PaymentProvider
from app.clients.user_client import UserClient
from app.db import repository
from app.db.models import IdempotencyKey, Transaction, TransactionStatus
from app.models.schemas import PaymentRequest, PaymentResponse
from shared.errors import ConflictError
from shared.events import Channels, RedisEventBus
from shared.idempotency import IdempotencyLock
from shared.logging import get_logger
from shared.telemetry import current_trace_id, inject_context

logger = get_logger(__name__)

IDEMPOTENCY_KEY_TTL = timedelta(hours=24)


class PaymentOrchestrator:
    """The transaction state machine. Owns the sequence
    risk-check -> debit -> provider-capture and the compensating action
    (credit back) if capture fails after the debit already succeeded —
    see the Phase 1 write-up on why that compensation exists.

    trace_id is no longer threaded in from the route handler (Phase 1-3
    had it come from an X-Trace-Id header) — Phase 4 derives it directly
    from the active OpenTelemetry span, which is the actual source of
    truth now that FastAPI/httpx auto-instrumentation handles real trace
    propagation on every HTTP hop this service makes or receives."""

    def __init__(
        self,
        fraud_client: FraudClient,
        user_client: UserClient,
        provider: PaymentProvider,
        event_bus: RedisEventBus,
        idempotency_lock: IdempotencyLock,
    ) -> None:
        self._fraud_client = fraud_client
        self._user_client = user_client
        self._provider = provider
        self._event_bus = event_bus
        self._idempotency_lock = idempotency_lock

    async def create_payment(
        self, db: AsyncSession, payload: PaymentRequest, idempotency_key: str
    ) -> PaymentResponse:
        request_hash = _hash_request(payload)

        existing = await repository.get_idempotency_key(db, idempotency_key)
        if existing is not None:
            return _replay(existing, request_hash, idempotency_key)

        if not await self._idempotency_lock.acquire(idempotency_key):
            raise ConflictError(
                "a request with this idempotency key is already being processed, retry shortly"
            )

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
            await self._user_client.debit(
                transaction.user_id, transaction.amount, transaction.currency, transaction.id
            )
            debited = True
            transaction = await self._capture_with_provider(db, transaction)
        except ConflictError as exc:
            if debited:
                logger.warning("payment.compensating_debit", transaction_id=str(transaction.id))
                await self._user_client.credit(
                    transaction.user_id, transaction.amount, transaction.currency, transaction.id
                )
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
        result = await self._fraud_client.check_risk(
            transaction.id, transaction.user_id, transaction.amount, transaction.currency
        )
        if result.risk_level == "high":
            raise ConflictError(f"transaction blocked by fraud check: {result.rationale}")

    async def _capture_with_provider(self, db: AsyncSession, transaction: Transaction) -> Transaction:
        transaction = await repository.set_status(db, transaction, TransactionStatus.provider_pending)
        result = await self._provider.capture(transaction.id, transaction.amount, transaction.currency)
        if not result.success:
            raise ConflictError(result.failure_reason or "provider declined the payment")
        transaction = await repository.mark_completed(db, transaction, result.provider_reference)
        await self._publish_outcome(transaction)
        return transaction

    async def _publish_outcome(self, transaction: Transaction) -> None:
        channel = (
            Channels.PAYMENT_COMPLETED
            if transaction.status == TransactionStatus.completed
            else Channels.PAYMENT_FAILED
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
    return datetime.now(timezone.utc) + IDEMPOTENCY_KEY_TTL


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
