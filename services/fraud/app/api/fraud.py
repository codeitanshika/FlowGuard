import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.schemas import RiskAssessmentResponse
from app.services import fraud_service
from shared.schemas import Envelope

router = APIRouter(tags=["fraud"])


@router.get("/risk-assessments/{transaction_id}", response_model=Envelope[RiskAssessmentResponse])
async def get_risk_assessment(
    transaction_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> Envelope[RiskAssessmentResponse]:
    assessment = await fraud_service.get_assessment(db, transaction_id)
    return Envelope(data=RiskAssessmentResponse.model_validate(assessment))
