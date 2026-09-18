from app.core.config import Settings
from shared.rate_limit import FixedWindowRateLimiter

_limiter: FixedWindowRateLimiter | None = None


async def init_dependencies(settings: Settings) -> None:
    global _limiter
    _limiter = FixedWindowRateLimiter(settings.redis_url)
    await _limiter.connect()


async def shutdown_dependencies() -> None:
    if _limiter is not None:
        await _limiter.disconnect()


def get_rate_limiter() -> FixedWindowRateLimiter:
    if _limiter is None:
        raise RuntimeError("rate limiter not initialized — init_dependencies() must run at startup")
    return _limiter
