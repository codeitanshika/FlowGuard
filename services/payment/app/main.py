from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.payments import router as payments_router
from app.core.config import get_settings
from app.core.dependencies import init_dependencies, shutdown_dependencies
from app.db.models import Base
from app.db.session import engine, is_ready
from shared.exception_handlers import register_exception_handlers
from shared.health import build_health_router
from shared.logging import configure_logging
from shared.middleware import TraceIdMiddleware

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await init_dependencies()
    yield
    await shutdown_dependencies()


app = FastAPI(title="FlowGuard Payment Service", lifespan=lifespan)
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(is_ready))
app.include_router(payments_router)
