import redis.asyncio as redis


class IdempotencyLock:
    """Short-lived Redis mutex around processing a given idempotency key.

    Phase 1's idempotency handling had a real race: two concurrent
    requests carrying the same Idempotency-Key could both pass the
    "does a saved response already exist" check before either one
    finished and saved its response, so both would fully process —
    including, in Payment Service's case, both debiting the user. This
    lock closes that window: a request that can't acquire the lock for a
    key currently being processed is told to retry, rather than being
    allowed to race the request that's already in flight. The lock is
    intentionally separate from the durable idempotency record — it's a
    short-TTL mutex for the *processing window*, not the record of the
    outcome, which still lives in Postgres once processing completes."""

    def __init__(self, redis_url: str, ttl_seconds: int = 30) -> None:
        self._redis_url = redis_url
        self._ttl = ttl_seconds
        self._client: redis.Redis | None = None

    async def connect(self) -> None:
        self._client = redis.from_url(self._redis_url, decode_responses=True, protocol=2)
        await self._client.ping()

    async def disconnect(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def acquire(self, key: str) -> bool:
        if self._client is None:
            raise RuntimeError("idempotency lock not connected")
        acquired = await self._client.set(f"idempotency:lock:{key}", "1", nx=True, ex=self._ttl)
        return bool(acquired)

    async def release(self, key: str) -> None:
        if self._client is None:
            raise RuntimeError("idempotency lock not connected")
        await self._client.delete(f"idempotency:lock:{key}")
