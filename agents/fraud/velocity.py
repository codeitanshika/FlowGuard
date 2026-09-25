import time
import uuid

import redis.asyncio as redis


class VelocityTracker:
    """Redis sorted-set sliding window per user, keyed exactly as
    docs/architecture/05-database-schema.md's Ephemeral State table lists
    it: `velocity:{user_id}`. Score is the record time (epoch seconds),
    member is the transaction id — naturally unique, so a redelivered or
    duplicate event can't double count."""

    def __init__(self, redis_client: redis.Redis, window_seconds: int) -> None:
        self._redis = redis_client
        self._window_seconds = window_seconds

    def _key(self, user_id: uuid.UUID) -> str:
        return f"velocity:{user_id}"

    async def record_and_count(self, user_id: uuid.UUID, transaction_id: uuid.UUID) -> int:
        """Adds this transaction to the window, prunes anything older than
        the window, and returns the count including this one. The key's
        own TTL is refreshed to 2x the window purely as a safety net if a
        user simply stops transacting — ZREMRANGEBYSCORE is what actually
        keeps the count correct while they're active."""

        key = self._key(user_id)
        now = time.time()
        cutoff = now - self._window_seconds

        pipe = self._redis.pipeline()
        pipe.zadd(key, {str(transaction_id): now})
        pipe.zremrangebyscore(key, 0, cutoff)
        pipe.zcard(key)
        pipe.expire(key, self._window_seconds * 2)
        _, _, count, _ = await pipe.execute()
        return int(count)
