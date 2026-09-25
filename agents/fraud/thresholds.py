from pydantic import BaseModel, ConfigDict, Field


class VelocityThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_seconds: int = Field(default=180, ge=1)
    # velocity_count >= high freezes immediately, no LLM call — the
    # "14 transactions in 3 minutes, threshold is 10" example from
    # docs/architecture/06-event-flows.md.
    high: int = Field(default=10, ge=1)
    # [borderline, high) gets an LLM narrative and is logged for review,
    # never auto-frozen — see ADR-0007 and ADR-0016.
    borderline: int = Field(default=6, ge=1)
    geo_mismatch_denominator: int = Field(default=10, ge=1)
