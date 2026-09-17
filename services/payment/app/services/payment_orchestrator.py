import hashlib
import json
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.fraud_client import FraudClient
from app.clients.provider_client import PaymentProvider
from app.clients.user_client import UserClient
from app.db import repository
from app.db.models import Transaction, TransactionStatus
from app.models.schemas import PaymentRequest, PaymentResponse
from shared.errors import ConflictError
from shared.events import Channels, RedisEventBus
from shared.logging import get_logger

logger = get_logger(__name__)

IDEMPOTENCY_KEY_TTL = timedelta(hours=24)


class PaymentOrchestrator:
    """The transaction state machine. Owns the sequence
    risk-check -> debit -> provider-capture and the compensating action
    (credit back) if capture fails after the debit already succeeded —
    see the Phase 1 write-up on why that compensation exists."""

    def __init__(
        self,
        fraud_client: FraudClient,
        user_client: UserClient,
        provider: PaymentProvider,
        event_bus: RedisEventBus,
    ) -> None:
        self._fraud_client = fraud_client
        self._user_client = user_client
        self._provider = provider
        self._event_bus = event_bus

    async def create_payment(
        self, db: AsyncSession, payload: PaymentRequest, idempotency_key: str, trace_id: str
    ) -> PaymentResponse:
        request_hash = _hash_request(payload)

        existing = await repository.get_idempotency_key(db, idempotency_key)
        if existing is not None:
            if existing.request_hash != request_hash:
                raise ConflictError("idempotency key already used with a different request body")
            logger.info("payment.idempotent_replay", idempotency_key=idempotency_key)
            return PaymentResponse(**existing.response_snapshot)

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
            {
                "event": Channels.PAYMENT_CREATED,
                "transaction_id": str(transaction.id),
                "user_id": str(transaction.user_id),
                "amount": str(transaction.amount),
                "currency": transaction.currency,
                "trace_id": trace_id,
                "timestamp": _now_iso(),
            },
        )

        debited = False
        try:
            await self._run_risk_check(db, transaction)
            await self._user_client.debit(
                transaction.user_id, transaction.amount, transaction.currency, transaction.id
            )
            debited = True
            transaction = await self._capture_with_provider(db, transaction, trace_id)
        except ConflictError as exc:
            if debited:
                logger.warning("payment.compensating_debit", transaction_id=str(transaction.id))
                await self._user_client.credit(
                    transaction.user_id, transaction.amount, transaction.currency, transaction.id
                )
            transaction = await repository.mark_failed(db, transaction, str(exc))
            await self._publish_outcome(transaction, trace_id)

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

    async def _capture_with_provider(
        self, db: AsyncSession, transaction: Transaction, trace_id: str
    ) -> Transaction:
        transaction = await repository.set_status(db, transaction, TransactionStatus.provider_pending)
        result = await self._provider.capture(transaction.id, transaction.amount, transaction.currency)
        if not result.success:
            raise ConflictError(result.failure_reason or "provider declined the payment")
        transaction = await repository.mark_completed(db, transaction, result.provider_reference)
        await self._publish_outcome(transaction, trace_id)
        return transaction

    async def _publish_outcome(self, transaction: Transaction, trace_id: str) -> None:
        channel = (
            Channels.PAYMENT_COMPLETED
            if transaction.status == TransactionStatus.completed
            else Channels.PAYMENT_FAILED
        )
        await self._event_bus.publish(
            channel,
            {
                "event": channel,
                "transaction_id": str(transaction.id),
                "user_id": str(transaction.user_id),
                "status": transaction.status.value,
                "provider_reference": transaction.provider_reference,
                "failure_reason": transaction.failure_reason,
                "trace_id": trace_id,
                "timestamp": _now_iso(),
            },
        )


def _hash_request(payload: PaymentRequest) -> str:
    canonical = json.dumps(payload.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _expiry() -> datetime:
    return datetime.now(timezone.utc) + IDEMPOTENCY_KEY_TTL


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
