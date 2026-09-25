import asyncio
from typing import Any, Awaitable, Callable

import redis.exceptions

from shared.events.bus import RedisEventBus
from shared.logging import get_logger

logger = get_logger(__name__)

RECONNECT_DELAY_SECONDS = 2.0

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


async def consume_forever(
    bus: RedisEventBus,
    *channels: str,
    on_message: MessageHandler,
    service_name: str,
    reconnect_delay_seconds: float = RECONNECT_DELAY_SECONDS,
) -> None:
    """Subscribes to `channels` and calls `on_message(message)` for each
    one, forever — resubscribing with a short delay if the Redis
    connection itself drops, instead of letting that exception propagate
    out and permanently kill the consumer task.

    Found the hard way: every consumer built before this (Healer, Fraud
    Agent, Notification) called `bus.subscribe()` exactly once and looped
    on `pubsub.get_message()` with no reconnect logic. A chaos test that
    restarted Redis mid-run killed the Healer's consumer task outright —
    logged loudly (`*.loop_terminated`, since main.py's done-callback
    does catch and log a dead task), but the process kept reporting
    itself healthy via /health while doing nothing for the rest of its
    life, because health checks a process's own liveness, not whether
    its background loop is still consuming. See docs/decisions/ADR-0018.

    `on_message` handles exactly one raw pub/sub message and is
    responsible for its own error handling (poison-event validation,
    dispatch failures, etc.) — this function's only job is keeping the
    subscription itself alive."""

    while True:
        try:
            pubsub = await bus.subscribe(*channels)
            logger.info("consumer.subscribed", service=service_name, channels=list(channels))
            while True:
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message is None:
                    continue
                await on_message(message)
        except asyncio.CancelledError:
            raise
        except redis.exceptions.RedisError as exc:
            logger.error("consumer.redis_connection_lost", service=service_name, error=str(exc))
            await asyncio.sleep(reconnect_delay_seconds)
