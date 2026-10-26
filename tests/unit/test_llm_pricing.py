from decimal import Decimal

from shared.llm_pricing import cost_usd


def test_known_model_computes_blended_cost():
    # 1000 input tokens + 200 output tokens at $5/$25 per 1M.
    got = cost_usd("claude-opus-5", 1000, 200)
    assert got == (Decimal(1000) * Decimal("5.00") + Decimal(200) * Decimal("25.00")) / Decimal(1_000_000)


def test_unknown_model_returns_none():
    assert cost_usd("some-future-model", 1000, 200) is None


def test_missing_tokens_returns_none():
    assert cost_usd("claude-opus-5", None, 200) is None
    assert cost_usd("claude-opus-5", 1000, None) is None


def test_missing_model_returns_none():
    assert cost_usd(None, 1000, 200) is None
