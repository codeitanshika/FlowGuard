from collections.abc import Awaitable, Callable

from fastapi import APIRouter
from fastapi.responses import JSONResponse


def build_health_router(readiness_check: Callable[[], Awaitable[bool]]) -> APIRouter:
    """Every service/agent gets /health (liveness) and /ready (readiness)
    from this one factory instead of reimplementing them. readiness_check
    is service-specific: a DB ping for a persistence-backed service, a
    downstream health sweep for the Gateway."""

    router = APIRouter()

    @router.get("/health")
    async def health() -> dict:
        return {"data": {"status": "ok"}, "error": None}

    @router.get("/ready")
    async def ready() -> JSONResponse:
        if not await readiness_check():
            return JSONResponse(
                status_code=503,
                content={
                    "data": None,
                    "error": {
                        "code": "DEPENDENCY_UNAVAILABLE",
                        "message": "one or more dependencies are not reachable",
                    },
                },
            )
        return JSONResponse(content={"data": {"status": "ready"}, "error": None})

    return router
