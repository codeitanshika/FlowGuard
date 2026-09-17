import uuid

import httpx
from fastapi import APIRouter, Request, Response

from shared.errors import DependencyUnavailableError, NotFoundError

_HOP_BY_HOP_HEADERS = {"host", "content-length", "connection"}


def build_proxy_router(route_table: dict[str, str]) -> APIRouter:
    router = APIRouter()

    @router.api_route(
        "/api/v1/{full_path:path}",
        methods=["GET", "POST", "PATCH", "DELETE", "PUT"],
    )
    async def proxy(full_path: str, request: Request) -> Response:
        target_base = _resolve_base_url(full_path, route_table)
        trace_id = request.headers.get("X-Trace-Id", str(uuid.uuid4()))

        headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP_HEADERS}
        headers["X-Trace-Id"] = trace_id

        body = await request.body()

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                upstream = await client.request(
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
            headers={"X-Trace-Id": trace_id},
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
