import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict

Confidence = Literal["low", "medium", "high"]
Action = Literal["open-circuit", "reset-circuit", "escalate"]


class AnomalyEvent(BaseModel):
    """`anomaly.detected` as published by the Monitor. Validated on receipt:
    a malformed event is logged and dropped, never allowed to crash the
    consumer loop (failure scenario 10)."""

    model_config = ConfigDict(extra="ignore")

    event: Literal["anomaly.detected"]
    anomaly_id: uuid.UUID
    service: str
    metric: Literal["error_rate", "p95_latency", "throughput"]
    observed_value: float
    threshold: float
    severity: Literal["warning", "critical"]
    trace_id: str | None = None


class HealerDecision(BaseModel):
    """The only thing an LLM is ever allowed to produce: data. It is
    validated by the planner and again by the Ops Controller before
    anything happens; nothing in here is executed as text."""

    root_cause: str
    confidence: Confidence
    action: Action
    # Only meaningful for open-circuit / reset-circuit.
    service: str | None = None
    dependency: str | None = None
    reasoning: str
