from typing import Literal

from agents.monitor.detector import compute_snapshot, evaluate, percentile
from agents.monitor.metrics_client import JaegerMetricsClient
from agents.monitor.thresholds import Thresholds

Outcome = Literal["recovered", "still_failing", "no_traffic"]


async def check_recovery(
    metrics: JaegerMetricsClient,
    service: str,
    metric: str,
    thresholds: Thresholds,
    window_seconds: int,
) -> Outcome:
    """Re-measures the affected service with the Monitor's own detection
    code, so "recovered" means exactly "the Monitor would not alert on it".
    Too little traffic is its own outcome: silence is not evidence of
    health."""

    samples, truncated = await metrics.fetch_samples(service, window_seconds)
    snapshot = compute_snapshot(service, samples, window_seconds, truncated)
    if snapshot.request_count < thresholds.min_requests:
        return "no_traffic"
    still_breaching = any(b.metric == metric for b in evaluate(snapshot, thresholds))
    return "still_failing" if still_breaching else "recovered"


async def check_still_failing(
    metrics: JaegerMetricsClient,
    service: str,
    metric: str,
    thresholds: Thresholds,
    window_seconds: int,
    last_n: int,
) -> Outcome:
    """Staleness guard run just before a disruptive action. An anomaly is
    computed over a trailing window, so by the time the Healer sees it the
    problem may already be over; acting then (e.g. force-opening a breaker
    on a dependency that just recovered) would cause the very outage it is
    meant to fix. Looks only at the most recent `last_n` requests rather
    than a rate over a window, so it works at low traffic and right at the
    boundary of a recovery."""

    samples, _ = await metrics.fetch_samples(service, window_seconds)
    if not samples:
        return "no_traffic"
    recent = sorted(samples, key=lambda s: s.start_us, reverse=True)[:last_n]
    if metric == "error_rate":
        return "still_failing" if any(s.is_error for s in recent) else "recovered"
    p95 = percentile(sorted(s.duration_ms for s in recent), 95)
    return "still_failing" if p95 >= thresholds.p95_warning_ms else "recovered"
