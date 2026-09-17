import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import RiskAssessment, RiskLevel


async def create_assessment(
    db: AsyncSession,
    *,
    transaction_id: uuid.UUID,
    user_id: uuid.UUID,
    risk_score: Decimal,
    risk_level: RiskLevel,
    rationale: str,
    rule_version: str,
) -> RiskAssessment:
    assessment = RiskAssessment(
        transaction_id=transaction_id,
        user_id=user_id,
        risk_score=risk_score,
        risk_level=risk_level,
        rationale=rationale,
        rule_version=rule_version,
    )
    db.add(assessment)
    await db.commit()
    await db.refresh(assessment)
    return assessment


async def get_by_transaction(db: AsyncSession, transaction_id: uuid.UUID) -> RiskAssessment | None:
    stmt = select(RiskAssessment).where(RiskAssessment.transaction_id == transaction_id)
    result = await db.execute(stmt)
    return result.scalars().first()
