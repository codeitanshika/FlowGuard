"""Prometheus exposition of the snapshot the Monitor already computes each
poll cycle (agent.py._check_service) — no second Jaeger query path, just a
second reader of the same numbers. Gauges, not Counters: request_count and
throughput are over a sliding window and can legitimately go down between
scrapes, which isn't what a Counter means."""

from prometheus_client import Gauge

from agents.monitor.detector import MetricsSnapshot

_LABELS = ("service",)

ERROR_RATE = Gauge("flowguard_error_rate_ratio", "Fraction of requests that errored in the current window", _LABELS)
AVAILABILITY = Gauge("flowguard_availability_ratio", "1 - error rate (NFR1)", _LABELS)
P95_LATENCY_MS = Gauge("flowguard_p95_latency_ms", "p95 request latency in the current window", _LABELS)
P99_LATENCY_MS = Gauge("flowguard_p99_latency_ms", "p99 request latency in the current window", _LABELS)
THROUGHPUT_RPS = Gauge("flowguard_throughput_rps", "Requests/second in the current window", _LABELS)
REQUEST_COUNT = Gauge("flowguard_request_count", "Requests observed in the current window", _LABELS)
TELEMETRY_TRUNCATED = Gauge(
    "flowguard_telemetry_truncated", "1 if the window hit Jaeger's max_traces cap, else 0", _LABELS
)


def update(snapshot: MetricsSnapshot) -> None:
    labels = (snapshot.service,)
    ERROR_RATE.labels(*labels).set(snapshot.error_rate)
    AVAILABILITY.labels(*labels).set(1.0 - snapshot.error_rate)
    P95_LATENCY_MS.labels(*labels).set(snapshot.p95_ms)
    P99_LATENCY_MS.labels(*labels).set(snapshot.p99_ms)
    THROUGHPUT_RPS.labels(*labels).set(snapshot.throughput_rps)
    REQUEST_COUNT.labels(*labels).set(snapshot.request_count)
    TELEMETRY_TRUNCATED.labels(*labels).set(1 if snapshot.truncated else 0)
