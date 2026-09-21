import redis.asyncio as redis

from app.core.config import Settings
from shared.fault_injection import FaultInjector
from shared.rate_limit import FixedWindowRateLimiter

_limiter: FixedWindowRateLimiter | None = None
_redis: redis.Redis | None = None
_fault_injector: FaultInjector | None = None


async def init_dependencies(settings: Settings) -> None:
    global _limiter, _redis, _fault_injector
    _limiter = FixedWindowRateLimiter(settings.redis_url)
    await _limiter.connect()

    # Own connection for the fault injector (Phase 6) rather than reusing
    # the rate limiter's — FixedWindowRateLimiter doesn't expose its
    # underlying client, and this is a simple enough key-based op that a
    # second connection isn't worth threading that through.
    _redis = redis.from_url(settings.redis_url, decode_responses=True, protocol=2)
    await _redis.ping()
    _fault_injector = FaultInjector(settings.service_name, _redis, component="self")


async def shutdown_dependencies() -> None:
    if _limiter is not None:
        await _limiter.disconnect()
    if _redis is not None:
        await _redis.aclose()


def get_rate_limiter() -> FixedWindowRateLimiter:
    if _limiter is None:
        raise RuntimeError("rate limiter not initialized — init_dependencies() must run at startup")
    return _limiter


def get_fault_injector_self() -> FaultInjector:
    if _fault_injector is None:
        raise RuntimeError("fault injector not initialized — init_dependencies() must run at startup")
    return _fault_injector
