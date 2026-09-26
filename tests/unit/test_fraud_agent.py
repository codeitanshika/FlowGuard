"""The Fraud Agent's event-handling logic (agents/fraud/agent.py) with
fakes for the DB, freeze client, event bus, and Redis cooldown. Checks the
boundary ADR-0007 cares about: high always freezes (once, cooldown-bounded),
borderline never freezes, low writes nothing."""

import os
import uuid
from types import SimpleNamespace

os.environ.setdefault("FRAUD_AGENT_DATABASE_URL", "postgresql+asyncpg://a:b@localhost/db")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("FRAUD_AGENT_USER_SERVICE_URL", "http://localhost:8003")

import pytest

from agents.fraud import agent as agent_module
from agents.fraud.agent import FraudAgent
from agents.fraud.freeze_client import FreezeError
from agents.fraud.narrative import Narrative
from agents.fraud.schemas import PaymentCreatedEvent
from agents.fraud.thresholds import VelocityThresholds


class FakeDb:
    def __init__(self):
        self.rows: list[dict] = []

    async def record_decision(self, input_summary, decision, executed, **kwargs):
        row = {"input_summary": input_summary, "decision": decision, "executed": executed, **kwargs}
        self.rows.append(row)
        return uuid.uuid4()


class FakeBus:
    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def publish(self, channel, payload):
        self.events.append((channel, payload))


class FakeVelocity:
    def __init__(self, count: int):
        self._count = count
        self.calls = 0

    async def record_and_count(self, user_id, transaction_id):
        self.calls += 1
        return self._count


class FakeFreezeClient:
    def __init__(self, error: FreezeError | None = None):
        self.calls: list[uuid.UUID] = []
        self.error = error

    async def freeze(self, user_id, reason, risk_assessment_id=None):
        self.calls.append(user_id)
        if self.error:
            raise self.error
        return {"id": str(user_id), "status": "frozen"}


class FakeNarrator:
    def __init__(self, narrative: Narrative | None = None):
        self.calls = 0
        self._narrative = narrative or Narrative("elevated velocity", "medium", "rules")

    async def narrate(self, decision):
        self.calls += 1
        return self._narrative


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}
        self.deleted: list[str] = []

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, key):
        self.deleted.append(key)
        self.store.pop(key, None)


def event(amount="1.00") -> PaymentCreatedEvent:
    return PaymentCreatedEvent(
        event="payment.created",
        transaction_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        amount=amount,
        currency="USD",
        trace_id="t1",
    )


@pytest.fixture
def env(monkeypatch):
    fake_db = FakeDb()
    monkeypatch.setattr(agent_module, "db", fake_db)
    # The simulated geo signal fires for ~1 in 10 random UUIDs; with random
    # ids every test here would flake ~10% of the time. Off by default —
    # tests that exercise geo re-patch it explicitly.
    monkeypatch.setattr(agent_module, "is_geo_anomaly", lambda *a, **k: (False, "US", "US"))
    return SimpleNamespace(db=fake_db, bus=FakeBus(), redis=FakeRedis())


def make_agent(env, velocity_count, freeze_error=None, narrative=None, thresholds=None) -> FraudAgent:
    settings = SimpleNamespace(thresholds=thresholds or VelocityThresholds(), freeze_cooldown_seconds=300)
    freeze_client = FakeFreezeClient(freeze_error)
    narrator = FakeNarrator(narrative)
    a = FraudAgent(settings, env.bus, FakeVelocity(velocity_count), freeze_client, narrator, env.redis)
    a.freeze_client, a.narrator = freeze_client, narrator  # test-only handles
    return a


async def test_low_risk_writes_nothing_and_freezes_nobody(env):
    a = make_agent(env, velocity_count=1)
    await a._handle(event())

    assert env.db.rows == [] and env.bus.events == [] and a.freeze_client.calls == [] and a.narrator.calls == 0


async def test_high_velocity_freezes_once_and_publishes(env):
    a = make_agent(env, velocity_count=10)
    txn = event()
    await a._handle(txn)

    assert len(a.freeze_client.calls) == 1 and a.freeze_client.calls[0] == txn.user_id
    assert env.db.rows[0]["executed"] is True and env.db.rows[0]["decision"]["outcome"] == "frozen"
    channel, payload = env.bus.events[0]
    assert channel == "fraud.user_frozen" and payload["user_id"] == str(txn.user_id)
    assert a.narrator.calls == 0, "the LLM must never be asked about a case the rules already decided"


async def test_borderline_never_freezes_only_logs_for_review(env):
    a = make_agent(env, velocity_count=7)
    await a._handle(event())

    assert a.freeze_client.calls == [] and env.bus.events == []
    assert env.db.rows[0]["executed"] is False
    assert env.db.rows[0]["decision"]["level"] == "borderline"
    assert env.db.rows[0]["decision"]["outcome"] == "logged_for_review"
    assert a.narrator.calls == 1


async def test_borderline_narrative_never_escalates_the_freeze_decision(env):
    # Even a "high" confidence narrative from the LLM cannot make this freeze —
    # there is no path from Narrative back into the freeze decision at all.
    a = make_agent(env, velocity_count=7, narrative=Narrative("looks very suspicious", "high", "llm"))
    await a._handle(event())

    assert a.freeze_client.calls == [] and env.bus.events == []
    assert env.db.rows[0]["decision"]["confidence"] == "high"
    assert env.db.rows[0]["executed"] is False


async def test_repeated_high_risk_for_the_same_user_is_cooled_down(env):
    a = make_agent(env, velocity_count=10)
    user_id = uuid.uuid4()
    first = PaymentCreatedEvent(
        event="payment.created", transaction_id=uuid.uuid4(), user_id=user_id, amount="1", currency="USD"
    )
    second = PaymentCreatedEvent(
        event="payment.created", transaction_id=uuid.uuid4(), user_id=user_id, amount="1", currency="USD"
    )

    await a._handle(first)
    await a._handle(second)

    assert len(a.freeze_client.calls) == 1, "the second high-risk event within the cooldown must not re-freeze"
    assert env.db.rows[1]["decision"]["outcome"] == "skipped_cooldown"
    assert len([e for e in env.bus.events if e[0] == "fraud.user_frozen"]) == 1


async def test_freeze_failure_releases_the_cooldown_and_does_not_publish(env):
    a = make_agent(env, velocity_count=10, freeze_error=FreezeError(503, "user service down"))
    txn = event()
    await a._handle(txn)

    assert env.bus.events == []
    assert env.db.rows[0]["executed"] is False and "freeze_failed" in env.db.rows[0]["decision"]["outcome"]
    assert f"fraud:freeze_cooldown:{txn.user_id}" in env.redis.deleted


async def test_geo_anomaly_alone_is_logged_for_review_not_frozen(env, monkeypatch):
    monkeypatch.setattr(agent_module, "is_geo_anomaly", lambda *a, **k: (True, "US", "IN"))
    a = make_agent(env, velocity_count=1)
    await a._handle(event())

    assert a.freeze_client.calls == []
    assert env.db.rows[0]["decision"]["level"] == "borderline"
    assert env.db.rows[0]["input_summary"]["geo_anomaly"] is True


@pytest.mark.parametrize("raw", ["{not valid json", "{}", '{"event": "payment.completed"}', "null", "[]"])
async def test_invalid_or_wrong_shaped_event_is_dropped_not_raised(env, raw):
    a = make_agent(env, velocity_count=10)  # would freeze if it were ever reached
    await a.process_raw_message(raw)

    assert env.db.rows == [] and a.freeze_client.calls == []
