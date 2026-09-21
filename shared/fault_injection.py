import asyncio
import random
from dataclasses import dataclass
from typing import Callable, Literal

import redis.asyncio as redis
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from shared.logging import get_logger

logger = get_logger(__name__)

FaultMode = Literal["error_500", "latency", "timeout"]

# See ADR-0013: every fault is bounded in both duration and intensity so
# there's no way — accidental or otherwise — to leave one running
# indefinitely or configure one severe enough to be indistinguishable
# from actually taking a service down on purpose.
MAX_DURATION_SECONDS = 300  # 5 minutes
MAX_LATENCY_MS = 60_000  # 60 seconds

_EXEMPT_PATHS = {"/health", "/ready"}
# Every path prefix that must stay reachable no matter what a fault is
# configured to do to everything else — the actual control endpoints
# for clearing a fault, on every service that has one. The Gateway's
# debug endpoint lives at a different path than the other services'
# /internal/fault-injection (it's the one fault-injection control
# surface meant to be externally reachable at all — see
# services/gateway/app/api/debug.py), so it's listed here too even
# though only the Gateway will ever actually have a path under it.
_EXEMPT_PREFIXES = ("/internal/fault-injection", "/api/v1/debug")


class FaultInjected(Exception):
    """Raised by FaultInjector.maybe_apply() when an active fault applies
    to this call. Deliberately standalone (like BreakerOpenError) — this
    module has no dependency on any one service's error vocabulary;
    callers translate it at the call site (FaultInjectionMiddleware into
    a real-looking 500, MockPaymentProvider into DependencyUnavailableError
    so it counts toward the provider circuit breaker)."""


class FaultInjectionRequest(BaseModel):
    mode: FaultMode
    error_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    latency_ms: int = Field(default=0, ge=0, le=MAX_LATENCY_MS)
    duration_seconds: int = Field(default=30, ge=1, le=MAX_DURATION_SECONDS)
    # Every service uses "self" (its own inbound HTTP surface) except
    # Payment Service, which also accepts "provider" — the payment
    # provider isn't a separate network service to inject faults on via
    # HTTP, it's an in-process call (MockPaymentProvider), so it needs
    # its own FaultInjector instance rather than the inbound middleware.
    component: str = "self"


@dataclass
class FaultConfig:
    mode: FaultMode
    error_rate: float
    latency_ms: int


class FaultInjector:
    """Redis-backed fault configuration for one (service, component)
    pair. State lives at `fault:{service_name}:{component}`, set with a
    Redis TTL equal to duration_seconds — expiry is enforced by Redis
    itself deleting the key, not by this code remembering to check a
    timestamp and correctly clean up. That's deliberate: an expiry
    mechanism that depends on code remembering to run is exactly the
    kind of thing that fails silently under the conditions (a struggling,
    fault-injected service) it's most needed."""

    def __init__(self, service_name: str, redis_client: redis.Redis, component: str = "self") -> None:
        self._key = f"fault:{service_name}:{component}"
        self._redis = redis_client

    async def enable(self, mode: FaultMode, error_rate: float, latency_ms: int, duration_seconds: int) -> None:
        duration_seconds = min(max(duration_seconds, 1), MAX_DURATION_SECONDS)
        error_rate = max(0.0, min(error_rate, 1.0))
        latency_ms = max(0, min(latency_ms, MAX_LATENCY_MS))
        await self._redis.set(self._key, f"{mode}:{error_rate}:{latency_ms}", ex=duration_seconds)
        logger.warning(
            "fault_injection.enabled",
            key=self._key,
            mode=mode,
            error_rate=error_rate,
            latency_ms=latency_ms,
            duration_seconds=duration_seconds,
        )

    async def disable(self) -> None:
        await self._redis.delete(self._key)
        logger.info("fault_injection.disabled", key=self._key)

    async def current(self) -> FaultConfig | None:
        raw = await self._redis.get(self._key)
        if raw is None:
            return None
        mode, error_rate, latency_ms = raw.split(":")
        return FaultConfig(mode=mode, error_rate=float(error_rate), latency_ms=int(latency_ms))  # type: ignore[arg-type]

    async def maybe_apply(self) -> None:
        """Call this at the point a fault should apply. Raises
        FaultInjected for error_500 (caller decides what that means for
        them). For latency/timeout, sleeps in place and returns normally
        — this call never hangs unboundedly (capped by MAX_LATENCY_MS);
        whether that delay is enough to trip a caller's own timeout is up
        to the caller's configured timeout, which is exactly the point."""

        config = await self.current()
        if config is None:
            return
        if random.random() > config.error_rate:
            return
        if config.mode == "error_500":
            raise FaultInjected(f"injected {config.mode}")
        await asyncio.sleep(config.latency_ms / 1000)


class FaultInjectionMiddleware(BaseHTTPMiddleware):
    """Applies a service's "self" FaultInjector to every inbound request
    except health checks and the fault-injection control endpoint itself
    — a fault must never be able to lock an operator out of clearing it
    or make the service look unhealthy purely because a fault is
    configured (health/readiness should reflect the process, not
    deliberately-injected test conditions).

    Takes an `injector_provider` callable, not a FaultInjector instance
    directly: `app.add_middleware()` instantiates this class at import
    time, before each service's lifespan has run `init_dependencies()`
    and actually created its FaultInjector — passing the instance
    directly would either bind `None`/crash or (worse) silently close
    over a not-yet-connected object. Resolving it fresh on every request,
    the same way FastAPI's `Depends(get_orchestrator)` does for routes,
    sidesteps that entirely."""

    def __init__(self, app: ASGIApp, injector_provider: Callable[[], "FaultInjector"]) -> None:
        super().__init__(app)
        self._injector_provider = injector_provider

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path in _EXEMPT_PATHS or path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        try:
            await self._injector_provider().maybe_apply()
        except FaultInjected as exc:
            logger.warning("fault_injection.triggered", path=path, error=str(exc))
            return JSONResponse(
                status_code=500,
                content={
                    "data": None,
                    # Same code a real unhandled exception would produce
                    # (shared/exception_handlers.py) — deliberately
                    # indistinguishable from a genuine failure to the
                    # caller. The distinguishing detail lives in this
                    # service's own logs (fault_injection.triggered
                    # above), not in the response.
                    "error": {"code": "INTERNAL_ERROR", "message": "an unexpected error occurred"},
                },
            )

        return await call_next(request)
