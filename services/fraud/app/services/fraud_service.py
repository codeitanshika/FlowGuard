import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import repository
from app.db.models import RiskAssessment
from app.services.rule_engine import RULE_VERSION, score_transaction
from shared.errors import NotFoundError


async def run_risk_check(
    db: AsyncSession, transaction_id: uuid.UUID, user_id: uuid.UUID, amount: Decimal
) -> RiskAssessment:
    risk_score, risk_level, rationale = score_transaction(amount)
    return await repository.create_assessment(
        db,
        transaction_id=transaction_id,
        user_id=user_id,
        risk_score=risk_score,
        risk_level=risk_level,
        rationale=rationale,
        rule_version=RULE_VERSION,
    )


async def get_assessment(db: AsyncSession, transaction_id: uuid.UUID) -> RiskAssessment:
    assessment = await repository.get_by_transaction(db, transaction_id)
    if assessment is None:
        raise NotFoundError(f"no risk assessment for transaction {transaction_id}")
    return assessment
