import asyncio
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.internal import router as internal_router
from app.api.notifications import router as notifications_router
from app.consumers.event_consumer import run_consumer
from app.core.config import get_settings
from app.core.dependencies import (
    get_fault_injector,
    init_dependencies,
    shutdown_dependencies,
)
from app.db.models import Base
from app.db.session import engine, is_ready
from shared.events import RedisEventBus
from shared.exception_handlers import register_exception_handlers
from shared.fault_injection import FaultInjectionMiddleware
from shared.health import build_health_router
from shared.logging import configure_logging
from shared.middleware import TraceIdMiddleware
from shared.telemetry import (
    configure_tracing,
    instrument_fastapi,
    instrument_redis,
    instrument_sqlalchemy,
)

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)
configure_tracing(settings.service_name)
instrument_sqlalchemy(engine)
instrument_redis()

_bus = RedisEventBus(settings.redis_url)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await _bus.connect()
    await init_dependencies()
    consumer_task = asyncio.create_task(run_consumer(_bus))

    yield

    consumer_task.cancel()
    with suppress(asyncio.CancelledError):
        await consumer_task
    await shutdown_dependencies()
    await _bus.disconnect()


app = FastAPI(title="FlowGuard Notification Service", lifespan=lifespan)
instrument_fastapi(app)
app.add_middleware(FaultInjectionMiddleware, injector_provider=get_fault_injector)
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(is_ready))
app.include_router(notifications_router)
app.include_router(internal_router)
