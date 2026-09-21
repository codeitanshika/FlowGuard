import redis.asyncio as redis

from app.clients.fraud_client import FraudClient
from app.clients.provider_client import MockPaymentProvider
from app.clients.user_client import UserClient
from app.core.config import get_settings
from app.services.payment_orchestrator import PaymentOrchestrator
from shared.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from shared.events import RedisEventBus
from shared.idempotency import IdempotencyLock

_event_bus: RedisEventBus | None = None
_idempotency_lock: IdempotencyLock | None = None
_breaker_redis: redis.Redis | None = None
_breakers: dict[str, CircuitBreaker] | None = None
_orchestrator: PaymentOrchestrator | None = None


async def init_dependencies() -> None:
    global _event_bus, _idempotency_lock, _breaker_redis, _breakers, _orchestrator
    settings = get_settings()

    _event_bus = RedisEventBus(settings.redis_url)
    await _event_bus.connect()

    _idempotency_lock = IdempotencyLock(settings.redis_url)
    await _idempotency_lock.connect()

    # A dedicated connection for the breakers, same pattern as the event
    # bus and idempotency lock each owning their own — protocol=2 for the
    # same reason shared/events/bus.py pins it (RESP2 is the long-stable
    # path in redis-py; no need to risk RESP3 for a dependency this
    # narrow).
    _breaker_redis = redis.from_url(settings.redis_url, decode_responses=True, protocol=2)
    await _breaker_redis.ping()

    # Three independent breakers, not one shared breaker for "outbound
    # calls" in general — Fraud Service being slow must never trip the
    # breaker guarding User Service calls. See docs/architecture/04-lld.md.
    _breakers = {
        "fraud": CircuitBreaker("payment:fraud", _breaker_redis, CircuitBreakerConfig()),
        "user": CircuitBreaker("payment:user", _breaker_redis, CircuitBreakerConfig()),
        "provider": CircuitBreaker("payment:provider", _breaker_redis, CircuitBreakerConfig()),
    }

    _orchestrator = PaymentOrchestrator(
        fraud_client=FraudClient(settings.fraud_service_url),
        user_client=UserClient(settings.user_service_url),
        provider=MockPaymentProvider(),
        event_bus=_event_bus,
        idempotency_lock=_idempotency_lock,
        fraud_breaker=_breakers["fraud"],
        user_breaker=_breakers["user"],
        provider_breaker=_breakers["provider"],
    )


async def shutdown_dependencies() -> None:
    if _event_bus is not None:
        await _event_bus.disconnect()
    if _idempotency_lock is not None:
        await _idempotency_lock.disconnect()
    if _breaker_redis is not None:
        await _breaker_redis.aclose()


def get_orchestrator() -> PaymentOrchestrator:
    if _orchestrator is None:
        raise RuntimeError("orchestrator not initialized — init_dependencies() must run at startup")
    return _orchestrator


def get_breakers() -> dict[str, CircuitBreaker]:
    if _breakers is None:
        raise RuntimeError("breakers not initialized — init_dependencies() must run at startup")
    return _breakers
