import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from shared.telemetry import current_trace_id


class TraceIdMiddleware(BaseHTTPMiddleware):
    """Binds the current request's real OpenTelemetry trace ID (and the
    service name) into structlog's contextvars for the life of the
    request, so every log line written while handling it carries the
    same trace_id without threading it through every function call by
    hand — and, critically, is now the *same* ID Jaeger shows for this
    request's span, not a separate ID that happened to travel alongside
    it. Before Phase 4, this minted its own random UUID and read/wrote an
    X-Trace-Id header as the actual propagation mechanism; OTel's
    FastAPI/httpx auto-instrumentation now does real W3C traceparent
    propagation on every HTTP hop, so that's retired — the response
    header below is kept purely as a human convenience (copy it into
    Jaeger's search box), not something anything still relies on for
    correctness."""

    def __init__(self, app: ASGIApp, service_name: str) -> None:
        super().__init__(app)
        self._service_name = service_name

    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = current_trace_id()
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(trace_id=trace_id, service=self._service_name)
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        return response
