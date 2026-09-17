import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Reads X-Trace-Id if the caller (usually the Gateway) already set one,
    otherwise mints one. Binds it (and the service name) into structlog's
    contextvars for the life of the request, so every log line written
    while handling it carries the same trace_id without threading it
    through every function call by hand. Phase 4 replaces the ad-hoc ID
    with a real OpenTelemetry trace context; this is the groundwork."""

    def __init__(self, app: ASGIApp, service_name: str) -> None:
        super().__init__(app)
        self._service_name = service_name

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id, service=self._service_name)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
