"""Reusable chaos-scenario runner. A scenario injects a real fault (Phase
6), drives real traffic through the real stack, and measures wall-clock
MTTR against NFR11 (< 2 minutes, automated recovery in the local/demo
environment) — using authoritative anomalies/incidents timestamps from
flowguard_control (see db.py), not inferred from HTTP behavior, so this
is measuring what the Monitor->Healer->Ops Controller loop actually did,
not just "did traffic eventually succeed again".

All MTTR arithmetic uses wall-clock datetimes throughout (fault-injection
time, `detected_at`, `resolved_at`) so it stays correct regardless of how
long polling itself takes; `time.monotonic()` is used only for the
polling loops' own timeout bookkeeping, never for a reported duration."""

import asyncio
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

from tests.chaos import db
from tests.live_helpers import clear_fault, create_funded_user, enable_fault, make_payment

RESOLUTION_TIMEOUT_SECONDS = 120  # NFR11's own budget, measured from fault injection


@dataclass
class ChaosResult:
    scenario: str
    recovered: bool
    detect_seconds: float | None  # fault injected -> anomaly detected
    resolve_seconds: float | None  # fault injected -> incident resolved (the NFR11 number)
    outcome: str | None
    detail: str


def _drive_traffic(token: str, user_id: str, count: int, delay_seconds: float = 0.5) -> None:
    for _ in range(count):
        make_payment(token, user_id, "1.00", idempotency_key=f"chaos-{uuid.uuid4().hex}")
        time.sleep(delay_seconds)


async def _wait_for_anomaly_and_incident(service: str, metric: str, after: datetime, timeout_seconds: float):
    deadline = time.monotonic() + timeout_seconds
    anomaly = None
    while time.monotonic() < deadline:
        anomaly = await db.latest_anomaly_after(service, metric, after)
        if anomaly is not None:
            break
        await asyncio.sleep(3)
    if anomaly is None:
        return None, None

    incident = None
    while time.monotonic() < deadline:
        incident = await db.incident_for_anomaly(anomaly.id)
        if incident is not None and incident.resolved_at is not None:
            break
        await asyncio.sleep(3)
    return anomaly, incident


async def run_provider_outage_scenario(token: str) -> ChaosResult:
    """Failure scenario #1 (docs/architecture/07-failure-scenarios.md):
    payment provider sandbox down. Expect the provider breaker to open
    fast (independent of the Healer — see ADR-0012/Phase 5) and the
    Healer to find it already open, taking no redundant action, and
    verify containment quickly."""

    user_id = create_funded_user(token, "1000.00")
    fault_injected_at = db.utcnow()

    enable_fault(token, "payment-provider", "error_500", error_rate=1.0, duration_seconds=90)
    try:
        _drive_traffic(token, user_id, count=15)
        anomaly, incident = await _wait_for_anomaly_and_incident(
            "payment", "error_rate", fault_injected_at, RESOLUTION_TIMEOUT_SECONDS
        )
    finally:
        clear_fault(token, "payment-provider")

    return _to_result("provider_outage", fault_injected_at, anomaly, incident)


async def run_fraud_service_outage_scenario(token: str) -> ChaosResult:
    """Failure scenario #2: Fraud Service unavailable. A partial error
    rate (not 100%, so Payment's own graceful-degradation path — not the
    breaker — absorbs individual requests) should still get flagged and
    remediated by the Monitor/Healer loop."""

    user_id = create_funded_user(token, "1000.00")
    fault_injected_at = db.utcnow()

    enable_fault(token, "fraud", "error_500", error_rate=0.4, duration_seconds=90)
    try:
        _drive_traffic(token, user_id, count=25)
        anomaly, incident = await _wait_for_anomaly_and_incident(
            "fraud", "error_rate", fault_injected_at, RESOLUTION_TIMEOUT_SECONDS
        )
    finally:
        clear_fault(token, "fraud")

    return _to_result("fraud_service_outage", fault_injected_at, anomaly, incident)


def _to_result(name: str, fault_injected_at: datetime, anomaly, incident) -> ChaosResult:
    if anomaly is None:
        return ChaosResult(name, False, None, None, None, "no anomaly was detected within the timeout")

    detect_seconds = (anomaly.detected_at - fault_injected_at).total_seconds()
    if incident is None or incident.resolved_at is None:
        return ChaosResult(
            name, False, detect_seconds, None, None,
            f"anomaly {anomaly.id} detected after {detect_seconds:.1f}s but no incident resolved within the timeout",
        )

    resolve_seconds = (incident.resolved_at - fault_injected_at).total_seconds()
    outcome = incident.status.value
    recovered = outcome == "resolved"
    return ChaosResult(
        name, recovered, detect_seconds, resolve_seconds, outcome,
        f"anomaly {anomaly.id} -> incident {incident.id} ({outcome}) in {resolve_seconds:.1f}s "
        f"(detected after {detect_seconds:.1f}s)",
    )
