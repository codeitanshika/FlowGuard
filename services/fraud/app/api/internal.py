from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_fault_injector
from app.db.session import get_db
from app.models.schemas import RiskAssessmentResponse, RiskCheckRequest
from app.services import fraud_service
from shared.fault_injection import FaultInjectionRequest, FaultInjector
from shared.schemas import Envelope

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/risk-check", response_model=Envelope[RiskAssessmentResponse])
async def risk_check(payload: RiskCheckRequest, db: AsyncSession = Depends(get_db)) -> Envelope[RiskAssessmentResponse]:
    assessment = await fraud_service.run_risk_check(db, payload.transaction_id, payload.user_id, payload.amount)
    return Envelope(data=RiskAssessmentResponse.model_validate(assessment))


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
