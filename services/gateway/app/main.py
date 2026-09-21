from contextlib import asynccontextmanager
from functools import partial

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.debug import router as debug_router
from app.api.health import check_downstream
from app.api.proxy import build_proxy_router
from app.core.config import get_settings
from app.core.dependencies import get_fault_injector_self, init_dependencies, shutdown_dependencies
from app.core.routing import build_route_table
from shared.exception_handlers import register_exception_handlers
from shared.fault_injection import FaultInjectionMiddleware
from shared.health import build_health_router
from shared.logging import configure_logging
from shared.middleware import TraceIdMiddleware
from shared.telemetry import configure_tracing, instrument_fastapi, instrument_httpx, instrument_redis

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)
configure_tracing(settings.service_name)
instrument_httpx()
instrument_redis()

route_table = build_route_table(settings)
downstream_urls = [
    settings.payment_service_url,
    settings.user_service_url,
    settings.notification_service_url,
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # No DB/table of its own (still true — see NFR3) but Phase 2 gives it
    # a Redis connection for rate limiting, so it now needs a lifespan.
    await init_dependencies(settings)
    yield
    await shutdown_dependencies()


app = FastAPI(title="FlowGuard API Gateway", lifespan=lifespan)
instrument_fastapi(app)
app.add_middleware(FaultInjectionMiddleware, injector_provider=get_fault_injector_self)
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(partial(check_downstream, downstream_urls)))
app.include_router(auth_router)
# Must be registered before the catch-all proxy router below — the proxy
# matches /api/v1/{full_path:path} and would otherwise swallow
# /api/v1/debug/fault-inject and 404 it against the route table.
app.include_router(debug_router)
app.include_router(build_proxy_router(route_table))
