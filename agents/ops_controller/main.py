from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import FastAPI

from agents.ops_controller.api import router as ops_router
from agents.ops_controller.config import get_settings
from agents.ops_controller.db import engine, is_ready
from agents.ops_controller.executors import CircuitExecutor
from shared.control_plane import init_control_plane_schema
from shared.exception_handlers import register_exception_handlers
from shared.health import build_health_router
from shared.logging import configure_logging
from shared.middleware import TraceIdMiddleware
from shared.telemetry import configure_tracing, instrument_fastapi, instrument_httpx

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)
configure_tracing(settings.service_name)
instrument_httpx()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_control_plane_schema(engine)
    app.state.redis = redis.from_url(settings.redis_url, decode_responses=True, protocol=2)
    await app.state.redis.ping()
    http_client = httpx.AsyncClient(timeout=settings.request_timeout_seconds)
    app.state.executor = CircuitExecutor(settings, http_client)
    yield
    await http_client.aclose()
    await app.state.redis.aclose()


app = FastAPI(title="FlowGuard Ops Controller", lifespan=lifespan)
instrument_fastapi(app)
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(is_ready))
app.include_router(ops_router)
