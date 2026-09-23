"""Unit tests for the Monitor Agent's pure logic (agents/monitor). Run from
the repo root: python -m pytest tests/unit/test_monitor.py -v"""

import pytest

from agents.monitor.alert_state import AlertDeduper
from agents.monitor.detector import SpanSample, compute_snapshot, evaluate, percentile
from agents.monitor.metrics_client import parse_traces
from agents.monitor.thresholds import Thresholds, resolve_thresholds


def samples(n_ok: int, n_err: int = 0, duration_ms: float = 50.0) -> list[SpanSample]:
    out = [SpanSample(f"ok{i}", i, duration_ms, False) for i in range(n_ok)]
    out += [SpanSample(f"err{i}", 1000 + i, duration_ms, True) for i in range(n_err)]
    return out


def test_percentile_nearest_rank():
    values = [float(v) for v in range(1, 101)]
    assert percentile(values, 95) == 95.0
    assert percentile(values, 99) == 99.0
    assert percentile([], 95) == 0.0
    assert percentile([7.0], 99) == 7.0


def test_snapshot_metrics_and_example_traces():
    snap = compute_snapshot("payment", samples(8, 2), window_seconds=10)
    assert snap.request_count == 10
    assert snap.error_rate == pytest.approx(0.2)
    assert snap.throughput_rps == pytest.approx(1.0)
    assert snap.error_trace_id == "err1"  # most recent errored span


def test_healthy_service_has_no_breach():
    snap = compute_snapshot("payment", samples(50), window_seconds=60)
    assert evaluate(snap, Thresholds()) == []


def test_error_rate_severity_grading():
    warn = evaluate(compute_snapshot("p", samples(18, 2), 60), Thresholds())  # 10%
    crit = evaluate(compute_snapshot("p", samples(10, 10), 60), Thresholds())  # 50%
    assert [(b.metric, b.severity) for b in warn] == [("error_rate", "warning")]
    assert [(b.metric, b.severity) for b in crit] == [("error_rate", "critical")]
    assert crit[0].threshold == Thresholds().error_rate_critical


def test_tiny_sample_never_alerts():
    # 1 failure in 2 requests is 50%, but below min_requests it's noise.
    assert evaluate(compute_snapshot("p", samples(1, 1), 60), Thresholds()) == []


def test_latency_breach_uses_p95():
    snap = compute_snapshot("p", samples(20, duration_ms=4000.0), 60)
    breaches = evaluate(snap, Thresholds())
    assert [(b.metric, b.severity) for b in breaches] == [("p95_latency", "critical")]


def test_throughput_floor_is_opt_in_and_skipped_when_truncated():
    idle = compute_snapshot("p", [], window_seconds=60)
    assert evaluate(idle, Thresholds()) == []
    floor = Thresholds(min_throughput_rps=1.0)
    assert [b.metric for b in evaluate(idle, floor)] == ["throughput"]
    truncated = compute_snapshot("p", [], window_seconds=60, truncated=True)
    assert evaluate(truncated, floor) == []


def test_threshold_overrides_merge_over_defaults():
    resolved = resolve_thresholds("payment", Thresholds(), {"payment": {"p95_warning_ms": 200}})
    assert resolved.p95_warning_ms == 200
    assert resolved.error_rate_critical == Thresholds().error_rate_critical
    assert resolve_thresholds("user", Thresholds(), {"payment": {"p95_warning_ms": 200}}) == Thresholds()


def _span(service_pid, kind, path, duration_us, tags=None, start=2_000_000):
    base = [
        {"key": "span.kind", "value": kind},
        {"key": "http.target", "value": path},
    ]
    return {
        "traceID": "t1",
        "processID": service_pid,
        "operationName": f"GET {path}",
        "startTime": start,
        "duration": duration_us,
        "tags": base + (tags or []),
    }


def test_parse_traces_filters_service_kind_and_probe_paths():
    payload = {
        "data": [
            {
                "traceID": "t1",
                "processes": {"p1": {"serviceName": "payment"}, "p2": {"serviceName": "user"}},
                "spans": [
                    _span("p1", "server", "/payments", 120_000),
                    _span("p1", "internal", "/payments", 5_000),  # not a request
                    _span("p2", "server", "/internal/users/1/debit", 9_000),  # other service
                    _span("p1", "server", "/health", 1_000),  # probe
                    _span("p1", "server", "/internal/circuit-breakers/fraud/open", 1_000),  # control
                    _span("p1", "server", "/payments", 30_000, [{"key": "http.status_code", "value": 503}]),
                    _span("p1", "server", "/payments", 30_000, [{"key": "error", "value": True}]),
                ],
            }
        ]
    }
    parsed = parse_traces(payload, "payment")
    assert [(s.duration_ms, s.is_error) for s in parsed] == [(120.0, False), (30.0, True), (30.0, True)]


def test_parse_traces_handles_empty_response():
    assert parse_traces({"data": None}, "payment") == []


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, key):
        self.store.pop(key, None)


async def test_dedupe_suppresses_repeats_but_allows_escalation():
    deduper = AlertDeduper(FakeRedis(), cooldown_seconds=60)
    assert await deduper.try_claim("payment", "error_rate", "warning") is True
    assert await deduper.try_claim("payment", "error_rate", "warning") is False
    assert await deduper.try_claim("payment", "error_rate", "critical") is True  # escalation
    assert await deduper.try_claim("payment", "error_rate", "critical") is False
    assert await deduper.try_claim("payment", "p95_latency", "warning") is True  # independent metric
    assert await deduper.try_claim("user", "error_rate", "warning") is True  # independent service


async def test_release_lets_a_failed_alert_retry():
    deduper = AlertDeduper(FakeRedis(), cooldown_seconds=60)
    assert await deduper.try_claim("payment", "error_rate", "critical") is True
    await deduper.release("payment", "error_rate")
    assert await deduper.try_claim("payment", "error_rate", "critical") is True
