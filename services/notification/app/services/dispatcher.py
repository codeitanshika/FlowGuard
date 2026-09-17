import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository
from app.db.models import NotificationChannel, NotificationStatus
from shared.logging import get_logger

logger = get_logger(__name__)

# Phase 1 has exactly one channel: log + a persisted record. Real email/
# webhook delivery is a later addition behind the same dispatch() call —
# nothing about the event-consumption path changes when that's added.
CHANNEL = NotificationChannel.log


async def dispatch(db: AsyncSession, event_type: str, payload: dict) -> None:
    user_id = payload.get("user_id")
    if user_id is None:
        raise ValueError(f"event {event_type!r} is missing user_id")

    transaction_id = payload.get("transaction_id")
    template, body = _render(event_type, payload)

    logger.info("notification.sent", user_id=user_id, event_type=event_type, body=body)

    await repository.create_notification(
        db,
        user_id=uuid.UUID(user_id),
        transaction_id=uuid.UUID(transaction_id) if transaction_id else None,
        channel=CHANNEL,
        template=template,
        status=NotificationStatus.sent,
        payload=payload,
    )


def _render(event_type: str, payload: dict) -> tuple[str, str]:
    if event_type == "payment.completed":
        ref = payload.get("provider_reference", "unknown")
        return "payment_completed", f"Your payment {payload.get('transaction_id')} succeeded (ref: {ref})."
    if event_type == "payment.failed":
        reason = payload.get("failure_reason", "unknown reason")
        return "payment_failed", f"Your payment {payload.get('transaction_id')} failed: {reason}."
    if event_type == "fraud.user_frozen":
        reason = payload.get("reason", "suspicious activity")
        return "account_frozen", f"Your account was frozen: {reason}."
    return "unknown_event", f"Unhandled event type: {event_type}"
