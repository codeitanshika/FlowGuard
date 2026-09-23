import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
import redis.asyncio as redis
from fastapi import FastAPI

from agents.monitor.agent import MonitorAgent
from agents.monitor.alert_state import AlertDeduper
from agents.monitor.config import get_settings
from agents.monitor.db import Base, engine, is_ready
from agents.monitor.metrics_client import JaegerMetricsClient
from shared.events import RedisEventBus
from shared.exception_handlers import register_exception_handlers
from shared.health import build_health_router
from shared.logging import configure_logging, get_logger
from shared.middleware import TraceIdMiddleware
from shared.telemetry import configure_tracing, instrument_fastapi

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)
# Tracing is configured for the Monitor's own HTTP surface only. httpx and
# redis are deliberately not instrumented: the Monitor polls Jaeger every
# few seconds, and tracing those calls would feed the Monitor's own
# activity back into the telemetry it is analyzing.
configure_tracing(settings.service_name)

logger = get_logger(__name__)


def _log_if_task_ended(task: asyncio.Task) -> None:
    # A background task that dies is otherwise silent until GC (the same
    # trap that hid a bug in Phase 1) — and a dead Monitor means nothing is
    # watching the system, so make it loud.
    if not task.cancelled():
        logger.error("monitor.loop_terminated", error=str(task.exception()))


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    bus = RedisEventBus(settings.redis_url)
    await bus.connect()
    redis_client = redis.from_url(settings.redis_url, decode_responses=True, protocol=2)
    await redis_client.ping()
    http_client = httpx.AsyncClient(timeout=10.0)

    agent = MonitorAgent(
        settings,
        JaegerMetricsClient(settings.jaeger_query_url, settings.max_traces, http_client),
        AlertDeduper(redis_client, settings.alert_cooldown_seconds),
        bus,
    )
    task = asyncio.create_task(agent.run_forever())
    task.add_done_callback(_log_if_task_ended)

    yield

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await http_client.aclose()
    await redis_client.aclose()
    await bus.disconnect()


app = FastAPI(title="FlowGuard Monitor Agent", lifespan=lifespan)
instrument_fastapi(app)
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(is_ready))
