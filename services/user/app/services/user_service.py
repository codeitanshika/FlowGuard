import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository
from app.db.models import User, UserStatus
from shared.errors import ConflictError, NotFoundError


async def create_user(db: AsyncSession, email: str, full_name: str, currency: str) -> User:
    return await repository.create_user(db, email, full_name, currency)


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User:
    user = await repository.get_user(db, user_id)
    if user is None:
        raise NotFoundError(f"user {user_id} not found")
    return user


async def debit(db: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> User:
    user = await get_user(db, user_id)
    if user.status != UserStatus.active:
        raise ConflictError(f"user {user_id} is not active (status={user.status.value})")
    if user.balance < amount:
        raise ConflictError(f"user {user_id} has insufficient balance")
    return await repository.update_balance(db, user, user.balance - amount)


async def credit(db: AsyncSession, user_id: uuid.UUID, amount: Decimal) -> User:
    """Compensating action for a debit that must be reversed (e.g. the
    payment provider declined after Payment Service already debited the
    user). Deliberately does not gate on account status — reversing money
    back to the user must succeed even if the account is frozen."""

    user = await get_user(db, user_id)
    return await repository.update_balance(db, user, user.balance + amount)


async def freeze(
    db: AsyncSession,
    user_id: uuid.UUID,
    reason: str,
    source: str,
    risk_assessment_id: uuid.UUID | None,
) -> User:
    user = await get_user(db, user_id)
    return await repository.set_status(
        db, user, UserStatus.frozen, "freeze", reason, source, risk_assessment_id
    )


async def unfreeze(db: AsyncSession, user_id: uuid.UUID, reason: str, source: str) -> User:
    user = await get_user(db, user_id)
    return await repository.set_status(db, user, UserStatus.active, "unfreeze", reason, source, None)
