from functools import partial

from fastapi import FastAPI

from app.api.health import check_downstream
from app.api.proxy import build_proxy_router
from app.core.config import get_settings
from app.core.routing import build_route_table
from shared.exception_handlers import register_exception_handlers
from shared.health import build_health_router
from shared.logging import configure_logging
from shared.middleware import TraceIdMiddleware

settings = get_settings()
configure_logging(settings.service_name, settings.log_level)

route_table = build_route_table(settings)
downstream_urls = [
    settings.payment_service_url,
    settings.user_service_url,
    settings.notification_service_url,
]

# No lifespan/DB engine here — the Gateway is stateless by design (NFR3),
# holds no persistence of its own, and JWT auth (Phase 2) will keep it
# that way.
app = FastAPI(title="FlowGuard API Gateway")
app.add_middleware(TraceIdMiddleware, service_name=settings.service_name)
register_exception_handlers(app)
app.include_router(build_health_router(partial(check_downstream, downstream_urls)))
app.include_router(build_proxy_router(route_table))
