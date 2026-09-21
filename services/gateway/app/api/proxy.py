import httpx
from fastapi import APIRouter, Depends, Request, Response

from app.api.authorization import check_scope, required_scope
from app.api.security_deps import enforce_rate_limit
from app.models.schemas import AuthenticatedClient
from shared.errors import DependencyUnavailableError, NotFoundError, ValidationAppError

_HOP_BY_HOP_HEADERS = {"host", "content-length", "connection"}
_BODY_METHODS = {"POST", "PATCH", "PUT"}


def build_proxy_router(route_table: dict[str, str]) -> APIRouter:
    router = APIRouter()

    @router.api_route(
        "/api/v1/{full_path:path}",
        methods=["GET", "POST", "PATCH", "DELETE", "PUT"],
    )
    async def proxy(
        full_path: str,
        request: Request,
        client: AuthenticatedClient = Depends(enforce_rate_limit),
    ) -> Response:
        target_base = _resolve_base_url(full_path, route_table)

        resource = full_path.split("/", 1)[0]
        check_scope(client.scopes, required_scope(request.method, resource))

        if request.method in _BODY_METHODS:
            content_type = request.headers.get("content-type", "")
            if not content_type.startswith("application/json"):
                raise ValidationAppError("request body must be application/json")

        # No manual trace-id header here — HTTPXClientInstrumentor injects
        # a real W3C traceparent into this outgoing request automatically,
        # based on the span FastAPIInstrumentor already started for the
        # incoming request. TraceIdMiddleware sets X-Trace-Id on the
        # response for humans; nothing needs it forwarded to the backend.
        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}
        headers["X-Client-Id"] = client.client_id

        body = await request.body()

        try:
            # 20s, not 10s: must have real headroom over the worst-case
            # time a backend's own internal retry+backoff sequence can
            # take, not just a single downstream call's timeout — see
            # ADR-0012. Found via testing: 10s was too close to Payment's
            # own fraud-breaker retry budget, so the Gateway would
            # occasionally give up on a request Payment was about to
            # answer successfully (via graceful degradation) on its own.
            async with httpx.AsyncClient(timeout=20.0) as http_client:
                upstream = await http_client.request(
                    request.method,
                    f"{target_base}/{full_path}",
                    params=list(request.query_params.multi_items()),
                    headers=headers,
                    content=body,
                )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"upstream service unreachable: {exc}") from exc

        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    return router


def _resolve_base_url(full_path: str, route_table: dict[str, str]) -> str:
    segment = full_path.split("/", 1)[0]
    # Explicit guard, redundant with the route table simply not containing
    # "internal" — kept because it directly encodes the security invariant
    # from ADR-0008 rather than relying on it as an accidental byproduct.
    if segment == "internal":
        raise NotFoundError("internal routes are not reachable through the gateway")
    base_url = route_table.get(segment)
    if base_url is None:
        raise NotFoundError(f"no route configured for /api/v1/{full_path}")
    return base_url.rstrip("/")
