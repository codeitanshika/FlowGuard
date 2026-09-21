import redis.asyncio as redis

from app.clients.fraud_client import FraudClient
from app.clients.provider_client import MockPaymentProvider
from app.clients.user_client import UserClient
from app.core.config import get_settings
from app.services.payment_orchestrator import PaymentOrchestrator
from shared.circuit_breaker import CircuitBreaker, CircuitBreakerConfig
from shared.events import RedisEventBus
from shared.fault_injection import FaultInjector
from shared.idempotency import IdempotencyLock

_event_bus: RedisEventBus | None = None
_idempotency_lock: IdempotencyLock | None = None
_redis: redis.Redis | None = None
_breakers: dict[str, CircuitBreaker] | None = None
_fault_injectors: dict[str, FaultInjector] | None = None
_orchestrator: PaymentOrchestrator | None = None


async def init_dependencies() -> None:
    global _event_bus, _idempotency_lock, _redis, _breakers, _fault_injectors, _orchestrator
    settings = get_settings()

    _event_bus = RedisEventBus(settings.redis_url)
    await _event_bus.connect()

    _idempotency_lock = IdempotencyLock(settings.redis_url)
    await _idempotency_lock.connect()

    # One dedicated connection shared by the breakers (Phase 5) and the
    # fault injectors (Phase 6) — both are just simple key-based Redis
    # ops, no reason to open a second TCP connection just to keep them
    # conceptually separate the way the event bus and idempotency lock
    # are (those wrap different libraries' own connection semantics).
    # protocol=2 for the same reason shared/events/bus.py pins it (RESP2
    # is the long-stable path in redis-py).
    _redis = redis.from_url(settings.redis_url, decode_responses=True, protocol=2)
    await _redis.ping()

    # Three independent breakers, not one shared breaker for "outbound
    # calls" in general — Fraud Service being slow must never trip the
    # breaker guarding User Service calls. See docs/architecture/04-lld.md.
    _breakers = {
        "fraud": CircuitBreaker("payment:fraud", _redis, CircuitBreakerConfig()),
        "user": CircuitBreaker("payment:user", _redis, CircuitBreakerConfig()),
        "provider": CircuitBreaker("payment:provider", _redis, CircuitBreakerConfig()),
    }

    # "self" = this service's own inbound HTTP surface (FaultInjectionMiddleware);
    # "provider" = MockPaymentProvider's in-process capture() call, which
    # has no HTTP surface for middleware to sit in front of.
    _fault_injectors = {
        "self": FaultInjector(settings.service_name, _redis, component="self"),
        "provider": FaultInjector(settings.service_name, _redis, component="provider"),
    }

    _orchestrator = PaymentOrchestrator(
        fraud_client=FraudClient(settings.fraud_service_url),
        user_client=UserClient(settings.user_service_url),
        provider=MockPaymentProvider(_fault_injectors["provider"]),
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
    if _redis is not None:
        await _redis.aclose()


def get_orchestrator() -> PaymentOrchestrator:
    if _orchestrator is None:
        raise RuntimeError("orchestrator not initialized — init_dependencies() must run at startup")
    return _orchestrator


def get_breakers() -> dict[str, CircuitBreaker]:
    if _breakers is None:
        raise RuntimeError("breakers not initialized — init_dependencies() must run at startup")
    return _breakers


def get_fault_injector_self() -> FaultInjector:
    if _fault_injectors is None:
        raise RuntimeError("fault injectors not initialized — init_dependencies() must run at startup")
    return _fault_injectors["self"]


def get_fault_injectors() -> dict[str, FaultInjector]:
    if _fault_injectors is None:
        raise RuntimeError("fault injectors not initialized — init_dependencies() must run at startup")
    return _fault_injectors
