from fastapi import APIRouter, Depends

from app.core.dependencies import get_breakers
from app.models.schemas import BreakerStatus
from shared.circuit_breaker import CircuitBreaker
from shared.errors import NotFoundError
from shared.schemas import Envelope

# Never routed by the Gateway (see docs/architecture/03-service-boundaries.md)
# — reachable only on the internal network, by the Ops Controller
# (Phase 8) on the Healer Agent's behalf.
router = APIRouter(prefix="/internal", tags=["internal"])


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
