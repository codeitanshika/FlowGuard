import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Notification, NotificationChannel, NotificationStatus


async def create_notification(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    transaction_id: uuid.UUID | None,
    channel: NotificationChannel,
    template: str,
    status: NotificationStatus,
    payload: dict[str, Any],
) -> Notification:
    notification = Notification(
        user_id=user_id,
        transaction_id=transaction_id,
        channel=channel,
        template=template,
        status=status,
        payload=payload,
    )
    db.add(notification)
    await db.commit()
    await db.refresh(notification)
    return notification


async def list_by_user(db: AsyncSession, user_id: uuid.UUID) -> list[Notification]:
    stmt = select(Notification).where(Notification.user_id == user_id).order_by(Notification.created_at.desc())
    result = await db.execute(stmt)
    return list(result.scalars().all())
