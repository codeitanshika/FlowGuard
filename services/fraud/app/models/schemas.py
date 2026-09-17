import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class RiskCheckRequest(BaseModel):
    transaction_id: uuid.UUID
    user_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)


class RiskAssessmentResponse(BaseModel):
    id: uuid.UUID
    transaction_id: uuid.UUID
    user_id: uuid.UUID
    risk_score: Decimal
    risk_level: Literal["low", "medium", "high"]
    rationale: str
    rule_version: str
    created_at: datetime

    model_config = {"from_attributes": True}
