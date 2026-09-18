import time

import redis.asyncio as redis


class FixedWindowRateLimiter:
    """Redis-backed fixed-window counter. See ADR-0009 for why fixed-window
    over a sliding-log/token-bucket: it's two Redis commands (INCR + EXPIRE
    on the first hit) and good enough for this project's actual traffic
    shape — bursty-but-bounded test/demo load, not a high-precision SLA."""

    def __init__(self, redis_url: str, window_seconds: int = 60) -> None:
        self._redis_url = redis_url
        self._window = window_seconds
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        self._client = redis.from_url(self._redis_url, decode_responses=True, protocol=2)
        await self._client.ping()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def check(self, key: str, limit: int) -> bool:
        """Returns True if this call is within the limit (and counts
        toward it), False if the limit is already exceeded."""

        if self._client is None:
            raise RuntimeError("rate limiter not connected")

        bucket = int(time.time() // self._window)
        redis_key = f"ratelimit:{key}:{bucket}"

        count = await self._client.incr(redis_key)
        if count == 1:
            await self._client.expire(redis_key, self._window)
        return count <= limit
