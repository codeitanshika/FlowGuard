from fastapi import APIRouter, Depends

from app.core.dependencies import get_breakers, get_fault_injectors
from app.models.schemas import BreakerStatus
from shared.circuit_breaker import CircuitBreaker
from shared.errors import NotFoundError
from shared.fault_injection import FaultInjectionRequest, FaultInjector
from shared.schemas import Envelope

# Never routed by the Gateway (see docs/architecture/03-service-boundaries.md)
# — reachable only on the internal network, by the Ops Controller
# (Phase 8) on the Healer Agent's behalf.
router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/circuit-breakers", response_model=Envelope[dict[str, str]])
async def list_breakers(breakers: dict[str, CircuitBreaker] = Depends(get_breakers)) -> Envelope[dict[str, str]]:
    # Read-only: lets the Healer (via the Ops Controller) see current
    # breaker state before deciding whether an action is even needed.
    return Envelope(data={name: (await b.state()).value for name, b in breakers.items()})


@router.post("/circuit-breakers/{dependency}/open", response_model=Envelope[BreakerStatus])
async def open_breaker(
    dependency: str, breakers: dict[str, CircuitBreaker] = Depends(get_breakers)
) -> Envelope[BreakerStatus]:
    breaker = _resolve(dependency, breakers)
    await breaker.force_open()
    return Envelope(data=BreakerStatus(dependency=dependency, state=(await breaker.state()).value))


@router.post("/circuit-breakers/{dependency}/reset", response_model=Envelope[BreakerStatus])
async def reset_breaker(
    dependency: str, breakers: dict[str, CircuitBreaker] = Depends(get_breakers)
) -> Envelope[BreakerStatus]:
    breaker = _resolve(dependency, breakers)
    await breaker.reset()
    return Envelope(data=BreakerStatus(dependency=dependency, state=(await breaker.state()).value))


def _resolve(dependency: str, breakers: dict[str, CircuitBreaker]) -> CircuitBreaker:
    if dependency not in breakers:
        raise NotFoundError(f"no circuit breaker named '{dependency}'")
    return breakers[dependency]


@router.post("/fault-injection", response_model=Envelope[dict])
async def configure_fault(
    payload: FaultInjectionRequest, injectors: dict[str, FaultInjector] = Depends(get_fault_injectors)
) -> Envelope[dict]:
    # component defaults to "self" (this service's own inbound HTTP
    # surface); pass component="provider" to target MockPaymentProvider's
    # capture() call specifically — see provider_client.py.
    injector = _resolve_injector(payload.component, injectors)
    await injector.enable(payload.mode, payload.error_rate, payload.latency_ms, payload.duration_seconds)
    return Envelope(data={"status": "enabled", "component": payload.component})


@router.delete("/fault-injection", response_model=Envelope[dict])
async def clear_fault(
    component: str = "self", injectors: dict[str, FaultInjector] = Depends(get_fault_injectors)
) -> Envelope[dict]:
    injector = _resolve_injector(component, injectors)
    await injector.disable()
    return Envelope(data={"status": "cleared", "component": component})


def _resolve_injector(component: str, injectors: dict[str, FaultInjector]) -> FaultInjector:
    if component not in injectors:
        raise NotFoundError(f"no fault injector for component '{component}' (expected 'self' or 'provider')")
    return injectors[component]
