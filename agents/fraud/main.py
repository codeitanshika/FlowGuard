import asyncio
from contextlib import asynccontextmanager, suppress

import httpx
import redis.asyncio as redis
from fastapi import FastAPI

from agents.fraud.agent import FraudAgent
from agents.fraud.config import get_settings
from agents.fraud.db import engine, is_ready
from agents.fraud.freeze_client import FreezeClient
from agents.fraud.narrative import Narrator
from agents.fraud.velocity import VelocityTracker
from shared.control_plane import init_control_plane_schema
from shared.events import RedisEventBus
from shared.exception_handlers import register_exception_handlers
from shared.health import build_health_router
from shared.logging import configure_logging, get_logger
from shared.middleware import TraceIdMiddleware
from shared.telemetry import configure_tracing, instrument_fastapi

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)
configure_tracing(settings.service_name)

logger = get_logger(__name__)


def _log_if_task_ended(task: asyncio.Task) -> None:
    if not task.cancelled():
        logger.error("fraud_agent.loop_terminated", error=str(task.exception()))


def _build_llm_client():
    if not settings.anthropic_api_key:
        logger.warning("fraud_agent.llm_disabled", reason="ANTHROPIC_API_KEY not set; using rules-based narrative only")
        return None
    from anthropic import AsyncAnthropic

    logger.info("fraud_agent.llm_enabled", model=settings.llm_model)
    return AsyncAnthropic(api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds, max_retries=1)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_control_plane_schema(engine)

    bus = RedisEventBus(settings.redis_url)
    await bus.connect()
    redis_client = redis.from_url(settings.redis_url, decode_responses=True, protocol=2)
    await redis_client.ping()
    http_client = httpx.AsyncClient(timeout=10.0)

    agent = FraudAgent(
        settings,
        bus,
        VelocityTracker(redis_client, settings.thresholds.window_seconds),
        FreezeClient(settings.user_service_url, http_client),
        Narrator(_build_llm_client(), settings.llm_model),
        redis_client,
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


app = FastAPI(title="FlowGuard Fraud Agent", lifespan=lifespan)
instrument_fastapi(app)
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(is_ready))
