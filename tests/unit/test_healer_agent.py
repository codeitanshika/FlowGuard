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
    def __init__(self, facts=None, circuits=None):
        self._facts, self._circuits = facts or [], circuits or {}

    async def trace_facts(self, trace_id):
        return self._facts

    async def circuits(self):
        return self._circuits


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
    return SimpleNamespace(db=fake_db, bus=FakeBus(), checks=checks)


def make_agent(env, gatherer, ops, max_concurrent=10) -> HealerAgent:
    settings = SimpleNamespace(
        verify_interval_seconds=0.001, verify_window_seconds=30, verify_timeout_seconds=0.05,
        max_concurrent_incidents=max_concurrent, default_thresholds=Thresholds(), service_thresholds={},
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


async def test_ops_cooldown_is_treated_as_recently_applied(env):
    env.checks.append("recovered")
    ops = FakeOps(OpsError(429, "ran too recently"))
    agent = make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "closed"}), ops)
    await agent._handle(anomaly())

    assert env.db.final[0] == IncidentStatus.resolved and "moments ago" in env.db.final[2]
    assert not env.db.executed_marks


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


async def test_no_traffic_to_a_shielded_service_counts_as_contained(env):
    env.checks.append("no_traffic")
    agent = make_agent(env, FakeGatherer([], {"user": "closed"}), FakeOps())
    await agent._handle(anomaly(service="user"))

    assert env.db.final[0] == IncidentStatus.resolved and "contained" in env.db.final[2]


async def test_no_traffic_is_not_recovery_for_the_calling_service(env):
    env.checks.append("no_traffic")
    agent = make_agent(env, FakeGatherer([PROVIDER_FACT], {"provider": "closed"}), FakeOps())
    await agent._handle(anomaly(service="payment"))

    assert env.db.final[0] == IncidentStatus.failed


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
