import json

from app.db.session import SessionLocal
from app.services import dispatcher
from shared.events import Channels, RedisEventBus
from shared.logging import get_logger

logger = get_logger(__name__)


async def run_consumer(bus: RedisEventBus) -> None:
    """Background task, started in main.py's lifespan. Never crashes on a
    bad message — per docs/architecture/07-failure-scenarios.md scenario
    10 (poison event), an invalid payload is logged and dropped, not
    retried, so one malformed event can't take the whole consumer down.

    Uses get_message() polling rather than `async for m in pubsub.listen()`
    — redis-py's own recommended pattern for async consumers, and the one
    that's easiest to reason about: a bounded-timeout poll that naturally
    loops, versus a single indefinite blocking read.

    Note for anyone debugging a "messages published but never consumed"
    symptom here: check for a logger call passing `event=` as a kwarg
    anywhere in this codepath before assuming it's a Redis/asyncio issue.
    structlog's log methods take the event/message as their first
    positional arg named `event`; passing a keyword also named `event`
    raises TypeError, and if that TypeError happens inside this loop's own
    except-Exception handler, it kills the loop with no useful log output
    to explain why — this cost real debugging time once already."""

    pubsub = await bus.subscribe(
        Channels.PAYMENT_COMPLETED, Channels.PAYMENT_FAILED, Channels.FRAUD_USER_FROZEN
    )
    logger.info("consumer.started", channels=[
        Channels.PAYMENT_COMPLETED, Channels.PAYMENT_FAILED, Channels.FRAUD_USER_FROZEN
    ])

    while True:
        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message is None:
            continue

        try:
            payload = json.loads(message["data"])
        except (json.JSONDecodeError, TypeError) as exc:
            logger.error("event.invalid", error=str(exc), raw=message.get("data"))
            continue

        event_type = payload.get("event", "unknown")
        try:
            async with SessionLocal() as db:
                await dispatcher.dispatch(db, event_type, payload)
        except Exception as exc:  # noqa: BLE001 - consumer loop must never die
            logger.error("event.processing_failed", event_type=event_type, error=str(exc))
