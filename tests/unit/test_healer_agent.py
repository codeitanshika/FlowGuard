"""The Healer's incident state machine (agents/healer/agent.py) with fakes
for the DB, Ops Controller, telemetry and event bus. Every path must end in
a terminal incident state, and nothing may reach the Ops Controller unless
the plan says so."""

import os
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("HEALER_DATABASE_URL", "postgresql+asyncpg://a:b@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OPS_HEALER_TOKEN", "dev-only-token-123456")

import pytest

from agents.healer import agent as agent_module
from agents.healer.agent import HealerAgent
from agents.healer.context import SpanFact
from agents.healer.diagnosis import Diagnoser
from agents.healer.ops_client import OpsError
from agents.healer.schemas import AnomalyEvent
from agents.monitor.thresholds import Thresholds
from shared.control_plane import IncidentStatus


class FakeDb:
    def __init__(self):
        self.updates: list[tuple] = []
        self.decisions: list[dict] = []
        self.executed_marks: list = []

    async def create_incident(self, anomaly_id):
        return SimpleNamespace(id=uuid.uuid4())

    async def update_incident(self, incident_id, status, root_cause=None, action_taken=None):
        self.updates.append((status, root_cause, action_taken))
        if status in (IncidentStatus.resolved, IncidentStatus.failed):
            return datetime.now(timezone.utc)

    async def record_decision(self, incident_id, **kwargs):
        self.decisions.append(kwargs)
        return uuid.uuid4()

    async def mark_decision_executed(self, decision_id):
        self.executed_marks.append(decision_id)

    @property
    def final(self):
        return self.updates[-1]


class FakeBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def publish(self, channel, payload):
        self.events.append((channel, payload))


class FakeOps:
    def __init__(self, error: OpsError | None = None):
        self.calls: list[tuple] = []
        self.error = error

    async def actions(self):
        return []

    async def execute(self, action, service, dependency):
        self.calls.append((action, service, dependency))
        if self.error:
            raise self.error
        return {"status": "executed", "action_id": "act-1"}


class FakeGatherer:
    """`circuits` may be a single dict, or a list of dicts returned one per
    call (the last repeats) to model state changing between reads."""

    def __init__(self, facts=None, circuits=None):
        self._facts = facts or []
        self._circuits = circuits if isinstance(circuits, list) else [circuits or {}]

    async def trace_facts(self, trace_id):
        return self._facts

    async def circuits(self):
        return self._circuits.pop(0) if len(self._circuits) > 1 else self._circuits[0]


def anomaly(service="payment", metric="error_rate") -> AnomalyEvent:
    return AnomalyEvent(
        event="anomaly.detected", anomaly_id=uuid.uuid4(), service=service, metric=metric,
        observed_value=1.0, threshold=0.2, severity="critical", trace_id="t1",
    )


PROVIDER_FACT = SpanFact("payment", "POST /payments", True, 201, "dependency", "payment provider fault injected: x")


@pytest.fixture
def env(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(agent_module, "db", fake_db)
    checks: list[str] = []

    async def fake_check(*args, **kwargs):
        return checks.pop(0) if len(checks) > 1 else checks[0]

    monkeypatch.setattr(agent_module, "check_recovery", fake_check)

    prechecks: list[str] = ["still_failing"]

    async def fake_precheck(*args, **kwargs):
        return prechecks[0]

    monkeypatch.setattr(agent_module, "check_still_failing", fake_precheck)
    return SimpleNamespace(db=fake_db, bus=FakeBus(), checks=checks, prechecks=prechecks)


def make_agent(env, gatherer, ops, max_concurrent=10) -> HealerAgent:
    settings = SimpleNamespace(
        verify_interval_seconds=0.001, verify_window_seconds=30, verify_timeout_seconds=0.05,
        max_concurrent_incidents=max_concurrent, default_thresholds=Thresholds(), service_thresholds={},
        precheck_window_seconds=30, precheck_recent_samples=5,
    )
    return HealerAgent(settings, env.bus, gatherer, Diagnoser(None, "claude-opus-5"), ops, metrics=None)


def resolved_event(env):
    return next(p for c, p in env.bus.events if c == "incident.resolved")


async def test_unactionable_anomaly_escalates_and_never_touches_ops(env):
    ops = FakeOps()
    agent = make_agent(env, FakeGatherer(), ops)
    await agent._handle(anomaly(service="gateway"))

    assert ops.calls == []
    assert env.db.final[0] == IncidentStatus.failed and env.db.final[2].startswith("escalated")
    assert resolved_event(env)["outcome"] == "escalated"
    assert env.db.decisions[0]["executed"] is False


async def test_evidence_backed_decision_is_executed_then_verified(env):
    env.checks.append("recovered")
    ops = FakeOps()
    agent = make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "closed"}), ops)
    await agent._handle(anomaly())

    assert ops.calls == [("open-circuit", "payment", "provider")]
    assert env.db.executed_marks, "decision must be marked executed after the Ops call succeeds"
    assert env.db.final[0] == IncidentStatus.resolved
    assert resolved_event(env)["outcome"] == "resolved"
    assert [c for c, _ in env.bus.events][:1] == ["incident.diagnosing"]


async def test_already_open_breaker_is_not_reopened_but_is_verified(env):
    env.checks.append("recovered")
    ops = FakeOps()
    agent = make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "open"}), ops)
    await agent._handle(anomaly())

    assert ops.calls == []
    assert env.db.final[0] == IncidentStatus.resolved and "already open" in env.db.final[2]


async def test_ops_cooldown_with_breaker_still_in_target_state_counts_as_applied(env):
    env.checks.append("recovered")
    ops = FakeOps(OpsError(429, "ran too recently"))
    gatherer = FakeGatherer([PROVIDER_FACT], [{"provider": "closed"}, {"provider": "open"}])
    await make_agent(env, gatherer, ops)._handle(anomaly())

    assert env.db.final[0] == IncidentStatus.resolved and "already applied" in env.db.final[2]
    assert not env.db.executed_marks


async def test_ops_cooldown_with_breaker_not_in_target_state_fails_the_incident(env):
    # Something reset the breaker after the earlier action: the cooldown must
    # not be mistaken for "still in effect".
    env.checks.append("recovered")
    ops = FakeOps(OpsError(429, "ran too recently"))
    gatherer = FakeGatherer([PROVIDER_FACT], [{"provider": "closed"}, {"provider": "closed"}])
    await make_agent(env, gatherer, ops)._handle(anomaly())

    assert env.db.final[0] == IncidentStatus.failed and "blocked by the Ops cooldown" in env.db.final[2]


@pytest.mark.parametrize("error", [OpsError(403, "forbidden"), OpsError(0, "unreachable"), OpsError(503, "payment down")])
async def test_ops_failure_fails_the_incident_without_verifying(env, error):
    env.checks.append("recovered")
    agent = make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "closed"}), FakeOps(error))
    await agent._handle(anomaly())

    assert env.db.final[0] == IncidentStatus.failed and "not applied" in env.db.final[2]
    assert not env.db.executed_marks
    assert resolved_event(env)["outcome"] == "failed"


async def test_unrecovered_service_fails_the_incident_after_the_timeout(env):
    env.checks.append("still_failing")
    agent = make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "closed"}), FakeOps())
    await agent._handle(anomaly())

    assert env.db.final[0] == IncidentStatus.failed and "not verified" in env.db.final[2]


async def test_sustained_silence_resolves_a_shielded_incident_even_after_the_breaker_self_heals(env):
    # Found by a live chaos test: a breaker can close on its own (a
    # half-open probe against a *partial*-failure fault has a real chance
    # of succeeding) between one verify check and the next, with no
    # traffic left to observe either way by the time that happens. This
    # must still resolve — a version that required the breaker to still
    # be open here got an incident permanently stuck.
    env.checks.append("no_traffic")
    gatherer = FakeGatherer([], {"user": "closed"})
    await make_agent(env, gatherer, FakeOps())._handle(anomaly(service="user"))

    assert env.db.final[0] == IncidentStatus.resolved and "no reproducing traffic" in env.db.final[2]


async def test_sustained_silence_resolves_for_the_calling_service_too(env):
    # Not just the shielded-dependency case: a caller like payment with no
    # traffic to evaluate also has no current evidence of a problem. If the
    # issue is still real, the Monitor's own re-alert path catches it once
    # traffic resumes and actually fails again.
    env.checks.append("no_traffic")
    agent = make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "closed"}), FakeOps())
    await agent._handle(anomaly(service="payment"))

    assert env.db.final[0] == IncidentStatus.resolved and "no reproducing traffic" in env.db.final[2]


async def test_unexpected_crash_still_ends_in_a_terminal_state(env):
    class Boom(FakeGatherer):
        async def trace_facts(self, trace_id):
            raise RuntimeError("jaeger exploded")

    agent = make_agent(env, Boom(), FakeOps())
    await agent._handle(anomaly())

    assert env.db.final[0] == IncidentStatus.failed and "jaeger exploded" in env.db.final[2]
    assert resolved_event(env)["outcome"] == "failed"


async def test_overload_is_recorded_not_silently_dropped(env):
    agent = make_agent(env, FakeGatherer(), FakeOps(), max_concurrent=0)
    agent._spawn(anomaly())
    for task in list(agent._tasks):
        await task

    assert env.db.final[0] == IncidentStatus.failed and "max concurrent" in env.db.final[2]


@pytest.mark.parametrize("fresh,note", [
    ("recovered", "stale anomaly"),
    ("no_traffic", "no recent traffic"),
])
async def test_stale_anomaly_is_not_acted_on(env, fresh, note):
    env.prechecks[0] = fresh
    ops = FakeOps()
    agent = make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "closed"}), ops)
    await agent._handle(anomaly())

    assert ops.calls == [], "must not force-open a breaker on a dependency that has already recovered"
    assert env.db.final[0] == IncidentStatus.resolved and note in env.db.final[2]
    assert not env.db.executed_marks


async def test_precheck_telemetry_outage_does_not_block_the_action(env, monkeypatch):
    from agents.monitor.metrics_client import MetricsUnavailableError

    async def unavailable(*args, **kwargs):
        raise MetricsUnavailableError("jaeger down")

    monkeypatch.setattr(agent_module, "check_still_failing", unavailable)
    env.checks.append("recovered")
    ops = FakeOps()
    await make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "closed"}), ops)._handle(anomaly())

    assert ops.calls == [("open-circuit", "payment", "provider")]
