import redis.asyncio as redis

from shared.logging import get_logger

logger = get_logger(__name__)


class AlertDeduper:
    """Suppresses repeat alerts for the same (service, metric).

    Without this, a sustained outage would publish a fresh
    anomaly.detected every poll cycle and the Healer would be asked to
    remediate the same incident over and over. State is a Redis key with a
    TTL, so the cooldown expires by itself (same reasoning as fault
    injection in ADR-0013) and survives a Monitor restart — an in-memory
    set would re-alert every existing incident after each deploy.

    A warning that worsens to critical is let through once: escalation is
    new information, repetition is not."""

    def __init__(self, redis_client: redis.Redis, cooldown_seconds: int) -> None:
        self._redis = redis_client
        self._cooldown = cooldown_seconds

    @staticmethod
    def _key(service: str, metric: str) -> str:
        return f"monitor:alert:{service}:{metric}"

    async def try_claim(self, service: str, metric: str, severity: str) -> bool:
        key = self._key(service, metric)
        if await self._redis.set(key, severity, nx=True, ex=self._cooldown):
            return True
        if await self._redis.get(key) == "warning" and severity == "critical":
            await self._redis.set(key, severity, ex=self._cooldown)
            return True
        return False

    async def release(self, service: str, metric: str) -> None:
        """Called when persisting/publishing failed after a successful
        claim, so the next cycle retries instead of the alert being
        silently swallowed for a whole cooldown."""
        await self._redis.delete(self._key(service, metric))
