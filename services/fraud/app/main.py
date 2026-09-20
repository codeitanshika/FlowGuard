from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.fraud import router as fraud_router
from app.api.internal import router as internal_router
from app.core.config import get_settings
from app.db.models import Base
from app.db.session import engine, is_ready
from shared.exception_handlers import register_exception_handlers
from shared.health import build_health_router
from shared.logging import configure_logging
from shared.middleware import TraceIdMiddleware
from shared.telemetry import configure_tracing, instrument_fastapi, instrument_sqlalchemy

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)
configure_tracing(settings.service_name)
instrument_sqlalchemy(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(title="FlowGuard Fraud Service", lifespan=lifespan)
instrument_fastapi(app)
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(is_ready))
app.include_router(fraud_router)
app.include_router(internal_router)
