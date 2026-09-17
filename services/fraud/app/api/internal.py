from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.schemas import RiskAssessmentResponse, RiskCheckRequest
from app.services import fraud_service
from shared.schemas import Envelope

router = APIRouter(prefix="/internal", tags=["internal"])


@router.post("/risk-check", response_model=Envelope[RiskAssessmentResponse])
async def risk_check(
    payload: RiskCheckRequest, db: AsyncSession = Depends(get_db)
) -> Envelope[RiskAssessmentResponse]:
    assessment = await fraud_service.run_risk_check(
        db, payload.transaction_id, payload.user_id, payload.amount
    )
    return Envelope(data=RiskAssessmentResponse.model_validate(assessment))
