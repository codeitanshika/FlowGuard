import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import IdempotencyKey, Transaction, TransactionStatus


async def create_transaction(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    amount,
    currency: str,
    provider: str,
    idempotency_key: str,
) -> Transaction:
    txn = Transaction(
        user_id=user_id,
        amount=amount,
        currency=currency,
        provider=provider,
        idempotency_key=idempotency_key,
        status=TransactionStatus.pending,
    )
    db.add(txn)
    await db.commit()
    await db.refresh(txn)
    return txn


async def get_transaction(db: AsyncSession, transaction_id: uuid.UUID) -> Transaction | None:
    return await db.get(Transaction, transaction_id)


async def list_transactions(db: AsyncSession, user_id: uuid.UUID | None) -> list[Transaction]:
    stmt = select(Transaction)
    if user_id is not None:
        stmt = stmt.where(Transaction.user_id == user_id)
    stmt = stmt.order_by(Transaction.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def set_status(db: AsyncSession, txn: Transaction, status: TransactionStatus) -> Transaction:
    txn.status = status
    await db.commit()
    await db.refresh(txn)
    return txn


async def mark_completed(db: AsyncSession, txn: Transaction, provider_reference: str | None) -> Transaction:
    txn.status = TransactionStatus.completed
    txn.provider_reference = provider_reference
    await db.commit()
    await db.refresh(txn)
    return txn


async def mark_failed(db: AsyncSession, txn: Transaction, failure_reason: str) -> Transaction:
    txn.status = TransactionStatus.failed
    txn.failure_reason = failure_reason
    await db.commit()
    await db.refresh(txn)
    return txn


async def get_idempotency_key(db: AsyncSession, key: str) -> IdempotencyKey | None:
    return await db.get(IdempotencyKey, key)


async def save_idempotency_key(
    db: AsyncSession,
    key: str,
    transaction_id: uuid.UUID,
    request_hash: str,
    response_snapshot: dict[str, Any],
    expires_at: datetime,
) -> None:
    db.add(
        IdempotencyKey(
            key=key,
            transaction_id=transaction_id,
            request_hash=request_hash,
            response_snapshot=response_snapshot,
            expires_at=expires_at,
        )
    )
    await db.commit()
