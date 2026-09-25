import uuid
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PaymentCreatedEvent(BaseModel):
    """`payment.created` as published by Payment Service. Validated on
    receipt: a malformed event is logged and dropped, never allowed to
    crash the consumer loop (failure scenario 10)."""

    model_config = ConfigDict(extra="ignore")

    event: Literal["payment.created"]
    transaction_id: uuid.UUID
    user_id: uuid.UUID
    amount: Decimal
    currency: str
    trace_id: str | None = None


RiskLevel = Literal["low", "borderline", "high"]
Confidence = Literal["low", "medium", "high"]


class RiskDecision(BaseModel):
    """Purely deterministic — see risk.py and ADR-0007. `level` is the only
    thing that ever decides whether a freeze happens; nothing downstream
    of this (including the LLM) can change it."""

    level: RiskLevel
    velocity_count: int
    geo_anomaly: bool
    reasons: list[str]


class FraudNarrative(BaseModel):
    """The only thing an LLM ever produces for the Fraud Agent — and
    unlike the Healer's HealerDecision, there is no action field at all:
    it is structurally impossible for this schema to carry a freeze
    decision. ADR-0007's "never given direct write authority" is enforced
    by the shape of the data, not just by code that ignores an action
    field if present."""

    rationale: str = Field(max_length=500)
    confidence: Confidence
