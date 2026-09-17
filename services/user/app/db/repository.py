import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import FreezeEvent, User, UserStatus


async def create_user(db: AsyncSession, email: str, full_name: str, currency: str) -> User:
    user = User(email=email, full_name=full_name, currency=currency)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def get_user(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def update_balance(db: AsyncSession, user: User, new_balance: Decimal) -> User:
    user.balance = new_balance
    await db.commit()
    await db.refresh(user)
    return user


async def set_status(
    db: AsyncSession,
    user: User,
    new_status: UserStatus,
    action: str,
    reason: str,
    source: str,
    risk_assessment_id: uuid.UUID | None,
) -> User:
    user.status = new_status
    db.add(
        FreezeEvent(
            user_id=user.id,
            action=action,
            reason=reason,
            source=source,
            risk_assessment_id=risk_assessment_id,
        )
    )
    await db.commit()
    await db.refresh(user)
    return user
