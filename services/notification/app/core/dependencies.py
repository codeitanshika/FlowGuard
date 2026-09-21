import redis.asyncio as redis

from app.core.config import get_settings
from shared.fault_injection import FaultInjector

_fault_redis: redis.Redis | None = None
_fault_injector: FaultInjector | None = None


async def init_dependencies() -> None:
    global _fault_redis, _fault_injector
    settings = get_settings()

    # A dedicated connection, separate from the event bus's — same
    # pattern Payment Service uses (each concern owns its own Redis
    # connection rather than sharing one).
    _fault_redis = redis.from_url(settings.redis_url, decode_responses=True, protocol=2)
    await _fault_redis.ping()
    _fault_injector = FaultInjector(settings.service_name, _fault_redis)


async def shutdown_dependencies() -> None:
    if _fault_redis is not None:
        await _fault_redis.aclose()


def get_fault_injector() -> FaultInjector:
    if _fault_injector is None:
        raise RuntimeError("fault injector not initialized — init_dependencies() must run at startup")
    return _fault_injector
