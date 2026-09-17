import uuid
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    currency: str = Field(default="USD", min_length=3, max_length=3)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    full_name: str
    status: Literal["active", "frozen", "closed"]
    balance: Decimal
    currency: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BalanceResponse(BaseModel):
    balance: Decimal
    currency: str


class FreezeRequest(BaseModel):
    reason: str = Field(min_length=1)
    source: Literal["fraud_agent", "manual"]
    risk_assessment_id: uuid.UUID | None = None


class BalanceAdjustmentRequest(BaseModel):
    """Shared shape for both /debit and /credit — the direction is implied
    by which endpoint is called, not by a field on the body."""

    amount: Decimal = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    transaction_id: uuid.UUID
