from typing import Literal

from agents.monitor.detector import compute_snapshot, evaluate
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
