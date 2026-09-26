"""Unit tests for the Fraud Agent's decision core (agents/fraud): the
simulated geo signal, the Redis velocity window, the deterministic risk
policy, and the LLM narrator (real SDK over a mock transport)."""

import json
import time
import uuid

import httpx2
import pytest
from anthropic import AsyncAnthropic

from agents.fraud.geo import home_country, is_geo_anomaly
from agents.fraud.narrative import Narrator
from agents.fraud.risk import decide
from agents.fraud.schemas import RiskDecision
from agents.fraud.thresholds import VelocityThresholds
from agents.fraud.velocity import VelocityTracker

# --- geo -------------------------------------------------------------


def test_home_country_is_deterministic_and_stable_across_processes():
    user_id = uuid.uuid4()
    assert home_country(user_id) == home_country(user_id)
    # hashlib-based, not Python's randomized hash() — same value regardless
    # of PYTHONHASHSEED, unlike the previous/next call within one process.
    assert home_country(uuid.UUID(str(user_id))) == home_country(user_id)


def test_geo_anomaly_is_deterministic_per_transaction():
    user_id, txn_id = uuid.uuid4(), uuid.uuid4()
    first = is_geo_anomaly(user_id, txn_id)
    second = is_geo_anomaly(user_id, txn_id)
    assert first == second


def test_geo_anomaly_when_triggered_reports_a_different_country():
    user_id = uuid.uuid4()
    triggered = [is_geo_anomaly(user_id, uuid.uuid4()) for _ in range(500)]
    anomalies = [t for t in triggered if t[0]]
    assert anomalies, "expected at least one anomaly out of 500 draws at the default 1-in-10 rate"
    for _, home, observed in anomalies:
        assert observed != home


def test_geo_anomaly_rate_is_roughly_one_in_denominator():
    user_id = uuid.uuid4()
    denom = 10
    results = [is_geo_anomaly(user_id, uuid.uuid4(), denom)[0] for _ in range(2000)]
    rate = sum(results) / len(results)
    assert 1 / denom * 0.5 < rate < 1 / denom * 1.5


def test_geo_anomaly_never_fires_with_denominator_effectively_off():
    user_id = uuid.uuid4()
    # A huge denominator makes the trigger astronomically unlikely across a
    # small, fixed sample — not a proof, but a practical smoke test.
    results = [is_geo_anomaly(user_id, uuid.uuid4(), 10_000_000)[0] for _ in range(200)]
    assert not any(results)


# --- velocity ----------------------------------------------------------


class FakeRedis:
    """Sorted-set subset used by VelocityTracker, plus a minimal pipeline."""

    def __init__(self):
        self.zsets: dict[str, dict[str, float]] = {}
        self.ttls: dict[str, int] = {}

    async def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)
        return len(mapping)

    async def zremrangebyscore(self, key, min_, max_):
        z = self.zsets.get(key, {})
        before = len(z)
        self.zsets[key] = {m: s for m, s in z.items() if not (min_ <= s <= max_)}
        return before - len(self.zsets[key])

    async def zcard(self, key):
        return len(self.zsets.get(key, {}))

    async def expire(self, key, seconds):
        self.ttls[key] = seconds
        return True

    def pipeline(self):
        return FakePipeline(self)


class FakePipeline:
    def __init__(self, redis):
        self._redis = redis
        self._ops = []

    def zadd(self, key, mapping):
        self._ops.append(("zadd", key, mapping))
        return self

    def zremrangebyscore(self, key, min_, max_):
        self._ops.append(("zremrangebyscore", key, min_, max_))
        return self

    def zcard(self, key):
        self._ops.append(("zcard", key))
        return self

    def expire(self, key, seconds):
        self._ops.append(("expire", key, seconds))
        return self

    async def execute(self):
        results = []
        for op in self._ops:
            name, *args = op
            results.append(await getattr(self._redis, name)(*args))
        return results


async def test_velocity_counts_within_window():
    redis_client, tracker = FakeRedis(), VelocityTracker(FakeRedis(), window_seconds=180)
    tracker = VelocityTracker(redis_client, window_seconds=180)
    user_id = uuid.uuid4()
    counts = [await tracker.record_and_count(user_id, uuid.uuid4()) for _ in range(5)]
    assert counts == [1, 2, 3, 4, 5]


async def test_velocity_prunes_entries_older_than_the_window():
    redis_client = FakeRedis()
    tracker = VelocityTracker(redis_client, window_seconds=60)
    user_id = uuid.uuid4()
    key = f"velocity:{user_id}"

    now = time.time()
    redis_client.zsets[key] = {"old-1": now - 120, "old-2": now - 90, "recent": now - 10}
    count = await tracker.record_and_count(user_id, uuid.uuid4())
    assert count == 2  # "recent" + the one just added; both "old-*" pruned


async def test_velocity_is_independent_per_user():
    redis_client = FakeRedis()
    tracker = VelocityTracker(redis_client, window_seconds=180)
    a, b = uuid.uuid4(), uuid.uuid4()
    for _ in range(3):
        await tracker.record_and_count(a, uuid.uuid4())
    assert await tracker.record_and_count(b, uuid.uuid4()) == 1


async def test_velocity_does_not_double_count_the_same_transaction():
    redis_client = FakeRedis()
    tracker = VelocityTracker(redis_client, window_seconds=180)
    user_id, txn_id = uuid.uuid4(), uuid.uuid4()
    await tracker.record_and_count(user_id, txn_id)
    assert await tracker.record_and_count(user_id, txn_id) == 1


# --- risk policy ---------------------------------------------------------


def test_low_velocity_no_geo_is_low_risk():
    d = decide(1, False, VelocityThresholds())
    assert d.level == "low"


def test_borderline_velocity_is_borderline():
    d = decide(7, False, VelocityThresholds())
    assert d.level == "borderline" and d.reasons


def test_high_velocity_is_high_regardless_of_geo():
    for geo in (False, True):
        assert decide(10, geo, VelocityThresholds()).level == "high"


def test_geo_anomaly_alone_is_borderline_never_high():
    d = decide(1, True, VelocityThresholds())
    assert d.level == "borderline"


def test_boundary_values_are_inclusive():
    t = VelocityThresholds()
    assert decide(t.borderline, False, t).level == "borderline"
    assert decide(t.borderline - 1, False, t).level == "low"
    assert decide(t.high, False, t).level == "high"
    assert decide(t.high - 1, False, t).level == "borderline"


def test_reasons_are_never_empty():
    for count, geo in [(1, False), (7, False), (10, False), (1, True), (10, True)]:
        assert decide(count, geo, VelocityThresholds()).reasons


# --- narrator: LLM path over a mock transport -----------------------------


def message_response(text: str, stop_reason="end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 80, "output_tokens": 30},
    }


def make_narrator(handler) -> Narrator:
    client = AsyncAnthropic(
        api_key="test-key",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )
    return Narrator(client, "claude-opus-5")


GOOD = json.dumps({"rationale": "elevated transaction velocity for this user", "confidence": "medium"})
BORDERLINE = RiskDecision(level="borderline", velocity_count=7, geo_anomaly=False, reasons=["7 in 180s"])


async def test_llm_narrative_is_used_and_never_asked_for_a_decision():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json=message_response(GOOD))

    result = await make_narrator(handler).narrate(BORDERLINE)

    assert result.source == "llm" and result.confidence == "medium"
    assert (result.tokens_in, result.tokens_out) == (80, 30)
    properties = seen["body"]["output_config"]["format"]["schema"]["properties"]
    assert set(properties) == {"rationale", "confidence"}


@pytest.mark.parametrize(
    "response",
    [
        lambda r: httpx2.Response(500, json={"type": "error", "error": {"type": "api_error", "message": "boom"}}),
        lambda r: httpx2.Response(200, json=message_response("", stop_reason="refusal")),
        lambda r: httpx2.Response(200, json=message_response("not json at all")),
    ],
)
async def test_any_llm_failure_falls_back_to_a_rules_based_narrative(response):
    result = await make_narrator(response).narrate(BORDERLINE)
    assert result.source == "rules" and result.fallback_reason
    assert "180s" in result.rationale


async def test_without_a_client_the_rules_decide():
    result = await Narrator(None, "claude-opus-5").narrate(BORDERLINE)
    assert result.source == "rules" and "ANTHROPIC_API_KEY" in result.fallback_reason
