import math
from dataclasses import dataclass
from typing import Literal

from agents.monitor.thresholds import Thresholds

Severity = Literal["warning", "critical"]


@dataclass(frozen=True)
class SpanSample:
    """One inbound-request (server) span, reduced to what detection needs."""

    trace_id: str
    start_us: int
    duration_ms: float
    is_error: bool


@dataclass(frozen=True)
class MetricsSnapshot:
    service: str
    window_seconds: int
    request_count: int
    error_count: int
    error_rate: float
    p95_ms: float
    p99_ms: float
    throughput_rps: float
    # Traces worth opening in Jaeger for each kind of breach.
    error_trace_id: str | None
    slowest_trace_id: str | None
    truncated: bool = False


@dataclass(frozen=True)
class Breach:
    service: str
    metric: Literal["error_rate", "p95_latency", "throughput"]
    observed_value: float
    threshold: float
    severity: Severity
    trace_id: str | None


def percentile(sorted_values: list[float], pct: float) -> float:
    """Nearest-rank percentile over an already-sorted list."""
    if not sorted_values:
        return 0.0
    rank = max(1, math.ceil(pct / 100 * len(sorted_values)))
    return sorted_values[rank - 1]


def compute_snapshot(
    service: str, samples: list[SpanSample], window_seconds: int, truncated: bool = False
) -> MetricsSnapshot:
    count = len(samples)
    errors = [s for s in samples if s.is_error]
    durations = sorted(s.duration_ms for s in samples)
    latest_error = max(errors, key=lambda s: s.start_us) if errors else None
    slowest = max(samples, key=lambda s: s.duration_ms) if samples else None

    return MetricsSnapshot(
        service=service,
        window_seconds=window_seconds,
        request_count=count,
        error_count=len(errors),
        error_rate=(len(errors) / count) if count else 0.0,
        p95_ms=percentile(durations, 95),
        p99_ms=percentile(durations, 99),
        throughput_rps=count / window_seconds,
        error_trace_id=latest_error.trace_id if latest_error else None,
        slowest_trace_id=slowest.trace_id if slowest else None,
        truncated=truncated,
    )


def evaluate(snapshot: MetricsSnapshot, thresholds: Thresholds) -> list[Breach]:
    """Deterministic threshold checks. Error rate and p95 need at least
    min_requests samples to mean anything; the throughput floor is the
    opposite — silence is precisely what it detects — but it is skipped
    when the sample was truncated, since a capped query undercounts."""

    breaches: list[Breach] = []

    if snapshot.request_count >= thresholds.min_requests:
        error_breach = _graded(snapshot.error_rate, thresholds.error_rate_warning, thresholds.error_rate_critical)
        if error_breach:
            severity, threshold = error_breach
            breaches.append(
                Breach(
                    snapshot.service, "error_rate", snapshot.error_rate, threshold, severity, snapshot.error_trace_id
                )
            )

        latency_breach = _graded(snapshot.p95_ms, thresholds.p95_warning_ms, thresholds.p95_critical_ms)
        if latency_breach:
            severity, threshold = latency_breach
            breaches.append(
                Breach(snapshot.service, "p95_latency", snapshot.p95_ms, threshold, severity, snapshot.slowest_trace_id)
            )

    if (
        thresholds.min_throughput_rps is not None
        and not snapshot.truncated
        and snapshot.throughput_rps < thresholds.min_throughput_rps
    ):
        breaches.append(
            Breach(
                snapshot.service, "throughput", snapshot.throughput_rps, thresholds.min_throughput_rps, "warning", None
            )
        )

    return breaches


def _graded(value: float, warning: float, critical: float) -> tuple[Severity, float] | None:
    if value >= critical:
        return "critical", critical
    if value >= warning:
        return "warning", warning
    return None
