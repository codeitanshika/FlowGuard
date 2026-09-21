import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class PaymentRequest(BaseModel):
    user_id: uuid.UUID
    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    # No `provider` field yet — Phase 1 has exactly one configured provider
    # (MockPaymentProvider). Phase 10 adds real provider selection and,
    # with it, this field; see docs/decisions/ADR (provider abstraction).


class PaymentResponse(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    amount: Decimal
    currency: str
    status: Literal["pending", "risk_check", "provider_pending", "completed", "failed", "cancelled"]
    provider: str
    provider_reference: str | None = None
    failure_reason: str | None = None
    created_at: datetime

    model_config = {"from_attributes": True}


class RiskCheckResult(BaseModel):
    """Payment Service's own narrow view of Fraud Service's response — it
    only needs enough to decide whether to proceed, not the full
    RiskAssessment record that Fraud Service persists."""

    risk_score: Decimal
    risk_level: Literal["low", "medium", "high"]
    rationale: str


class BreakerStatus(BaseModel):
    dependency: Literal["fraud", "user", "provider"]
    state: Literal["closed", "open", "half_open"]
