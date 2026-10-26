"""Per-decision LLM cost in dollars — NFR10 says cost must be tracked per
decision; before this, only token counts were (agent_decisions.llm_tokens_*).
Prices are Anthropic's published per-million-token rates, current as of
2026-10 — not derived from any API response, so they drift out of date
silently if a model's price changes. Update PRICING_PER_MTOK when it does;
an unpriced or unknown model simply gets no cost figure (None), the same
"we don't know, say so" posture as every other optional field here rather
than guessing."""

from decimal import Decimal

# (input $ / 1M tokens, output $ / 1M tokens)
PRICING_PER_MTOK: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5.00"), Decimal("25.00")),
}


def cost_usd(model: str | None, tokens_in: int | None, tokens_out: int | None) -> Decimal | None:
    if model is None or tokens_in is None or tokens_out is None:
        return None
    prices = PRICING_PER_MTOK.get(model)
    if prices is None:
        return None
    price_in, price_out = prices
    million = Decimal(1_000_000)
    return (Decimal(tokens_in) * price_in + Decimal(tokens_out) * price_out) / million
