from pydantic import BaseModel, ConfigDict, Field


class Thresholds(BaseModel):
    """Per-service detection thresholds. Latency is in milliseconds,
    error rates are fractions (0.05 = 5%). min_requests guards against
    alerting on a tiny sample: 1 failure out of 2 requests is a 50% error
    rate but not a signal."""

    model_config = ConfigDict(extra="forbid")

    error_rate_warning: float = Field(default=0.05, ge=0.0, le=1.0)
    error_rate_critical: float = Field(default=0.20, ge=0.0, le=1.0)
    p95_warning_ms: float = Field(default=1000.0, gt=0)
    p95_critical_ms: float = Field(default=3000.0, gt=0)
    min_requests: int = Field(default=10, ge=1)
    # Opt-in: a throughput floor needs a traffic level that is actually
    # expected, which only the operator knows. Off by default so an idle
    # dev stack doesn't page anyone.
    min_throughput_rps: float | None = Field(default=None, ge=0)


def resolve_thresholds(
    service: str, defaults: Thresholds, overrides: dict[str, dict[str, float]]
) -> Thresholds:
    override = overrides.get(service)
    if not override:
        return defaults
    return Thresholds(**{**defaults.model_dump(), **override})
