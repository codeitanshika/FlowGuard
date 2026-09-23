import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI

from agents.healer.agent import HealerAgent
from agents.healer.config import get_settings
from agents.healer.context import ContextGatherer
from agents.healer.db import engine, fail_stale_incidents, is_ready
from agents.healer.diagnosis import Diagnoser
from agents.healer.ops_client import OpsClient
from agents.monitor.metrics_client import JaegerMetricsClient
from shared.control_plane import init_control_plane_schema
from shared.events import RedisEventBus
from shared.exception_handlers import register_exception_handlers
from shared.health import build_health_router
from shared.logging import configure_logging, get_logger
from shared.middleware import TraceIdMiddleware
from shared.telemetry import configure_tracing, instrument_fastapi

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)
# Like the Monitor: only the HTTP surface is traced, not the polling calls.
configure_tracing(settings.service_name)

logger = get_logger(__name__)


def _log_if_task_ended(task: asyncio.Task) -> None:
    if not task.cancelled():
        logger.error("healer.loop_terminated", error=str(task.exception()))


def _build_llm_client():
    if not settings.anthropic_api_key:
        logger.warning("healer.llm_disabled", reason="ANTHROPIC_API_KEY not set; using deterministic rules only")
        return None
    from anthropic import AsyncAnthropic

    logger.info("healer.llm_enabled", model=settings.llm_model)
    return AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_control_plane_schema(engine)
    abandoned = await fail_stale_incidents()
    if abandoned:
        logger.warning("healer.stale_incidents_closed", count=abandoned)

    bus = RedisEventBus(settings.redis_url)
    await bus.connect()
    http_client = httpx.AsyncClient(timeout=10.0)
    ops = OpsClient(settings.ops_controller_url, settings.ops_token, http_client)

    agent = HealerAgent(
        settings,
        bus,
        ContextGatherer(settings.jaeger_query_url, http_client, ops),
        Diagnoser(_build_llm_client(), settings.llm_model),
        ops,
        JaegerMetricsClient(settings.jaeger_query_url, 1000, http_client),
    )
    task = asyncio.create_task(agent.run_forever())
    task.add_done_callback(_log_if_task_ended)

    yield

    task.cancel()
    with suppress(asyncio.CancelledError):
        await task
    await agent.stop()
    await http_client.aclose()
    await bus.disconnect()


app = FastAPI(title="FlowGuard Healer Agent", lifespan=lifespan)
instrument_fastapi(app)
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(is_ready))
