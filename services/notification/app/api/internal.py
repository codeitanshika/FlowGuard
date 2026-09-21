from fastapi import APIRouter, Depends

from app.core.dependencies import get_fault_injector
from shared.fault_injection import FaultInjectionRequest, FaultInjector
from shared.schemas import Envelope

# Never routed by the Gateway (see docs/architecture/03-service-boundaries.md)
# — reachable only on the internal network.
router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/fault-injection", response_model=Envelope[dict])
async def configure_fault(
    payload: FaultInjectionRequest, injector: FaultInjector = Depends(get_fault_injector)
) -> Envelope[dict]:
    await injector.enable(payload.mode, payload.error_rate, payload.latency_ms, payload.duration_seconds)
    return Envelope(data={"status": "enabled"})


@router.delete("/fault-injection", response_model=Envelope[dict])
async def clear_fault(injector: FaultInjector = Depends(get_fault_injector)) -> Envelope[dict]:
    await injector.disable()
    return Envelope(data={"status": "cleared"})
