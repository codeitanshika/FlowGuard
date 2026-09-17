from app.clients.fraud_client import FraudClient
from app.clients.provider_client import MockPaymentProvider
from app.clients.user_client import UserClient
from app.core.config import get_settings
from app.services.payment_orchestrator import PaymentOrchestrator
from shared.events import RedisEventBus

_event_bus: RedisEventBus | None = None
_orchestrator: PaymentOrchestrator | None = None


async def init_dependencies() -> None:
    global _event_bus, _orchestrator
    settings = get_settings()

    _event_bus = RedisEventBus(settings.redis_url)
    await _event_bus.connect()

    _orchestrator = PaymentOrchestrator(
        fraud_client=FraudClient(settings.fraud_service_url),
        user_client=UserClient(settings.user_service_url),
        provider=MockPaymentProvider(),
        event_bus=_event_bus,
    )


async def shutdown_dependencies() -> None:
    if _event_bus is not None:
        await _event_bus.disconnect()


def get_orchestrator() -> PaymentOrchestrator:
    if _orchestrator is None:
        raise RuntimeError("orchestrator not initialized — init_dependencies() must run at startup")
    return _orchestrator
