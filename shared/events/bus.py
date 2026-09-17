import json
from typing import Any

import redis.asyncio as redis

from shared.logging import get_logger

logger = get_logger(__name__)


class RedisEventBus:
    """Thin wrapper over redis.asyncio pub/sub. Fire-and-forget by design —
    see ADR-0002: nothing on the payment critical path depends on delivery,
    so this deliberately does not attempt at-least-once/replay semantics."""

    def __init__(self, redis_url: str) -> None:
        self._redis_url = redis_url
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        # protocol=2 (RESP2) pinned deliberately: RESP2 pub/sub is the
        # long-stable code path in redis-py; RESP3 (the client's default
        # since redis-py 5) is newer and not worth the risk here for a
        # feature this narrow.
        self._client = redis.from_url(self._redis_url, decode_responses=True, protocol=2)
        await self._client.ping()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        if self._client is None:
            raise RuntimeError("event bus not connected")
        await self._client.publish(channel, json.dumps(payload, default=str))
        logger.info("event.published", channel=channel)

    async def subscribe(self, *channels: str) -> "redis.client.PubSub":
        if self._client is None:
            raise RuntimeError("event bus not connected")
        pubsub = self._client.pubsub()
        await pubsub.subscribe(*channels)
        return pubsub
