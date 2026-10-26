"""Verifies the Monitor's /metrics exposition actually reflects what
compute_snapshot produced — not just that update() runs without error."""

from prometheus_client import REGISTRY

from agents.monitor import metrics_export
from agents.monitor.detector import MetricsSnapshot


def _snapshot(**overrides) -> MetricsSnapshot:
    defaults = dict(
        service="payment",
        window_seconds=60,
        request_count=40,
        error_count=4,
        error_rate=0.1,
        p95_ms=250.0,
        p99_ms=400.0,
        throughput_rps=0.667,
        error_trace_id="err1",
        slowest_trace_id="slow1",
        truncated=False,
    )
    defaults.update(overrides)
    return MetricsSnapshot(**defaults)


def test_update_sets_gauges_readable_by_service_label():
    metrics_export.update(_snapshot())

    assert REGISTRY.get_sample_value("flowguard_error_rate_ratio", {"service": "payment"}) == 0.1
    assert REGISTRY.get_sample_value("flowguard_availability_ratio", {"service": "payment"}) == 0.9
    assert REGISTRY.get_sample_value("flowguard_p95_latency_ms", {"service": "payment"}) == 250.0
    assert REGISTRY.get_sample_value("flowguard_throughput_rps", {"service": "payment"}) == 0.667
    assert REGISTRY.get_sample_value("flowguard_telemetry_truncated", {"service": "payment"}) == 0


def test_update_is_keyed_per_service_and_does_not_clobber_others():
    metrics_export.update(_snapshot(service="payment", error_rate=0.1))
    metrics_export.update(_snapshot(service="gateway", error_rate=0.5))

    assert REGISTRY.get_sample_value("flowguard_error_rate_ratio", {"service": "payment"}) == 0.1
    assert REGISTRY.get_sample_value("flowguard_error_rate_ratio", {"service": "gateway"}) == 0.5


def test_truncated_flag_exposed_as_zero_or_one():
    metrics_export.update(_snapshot(service="notification", truncated=True))
    assert REGISTRY.get_sample_value("flowguard_telemetry_truncated", {"service": "notification"}) == 1
