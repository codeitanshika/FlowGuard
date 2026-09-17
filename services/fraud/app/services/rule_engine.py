from decimal import Decimal

from app.db.models import RiskLevel

RULE_VERSION = "v1"

# Phase 1 deliberately keeps this deterministic and amount-only — no
# velocity/geo signals yet. Those require behavioral history over time,
# which is exactly what the async Fraud Agent (Phase 9, Redis sliding
# windows) adds. This synchronous check only needs to be fast and
# self-contained, per docs/architecture/03-service-boundaries.md.
LARGE_TXN_THRESHOLD = Decimal("5000")
VERY_LARGE_TXN_THRESHOLD = Decimal("20000")


def score_transaction(amount: Decimal) -> tuple[Decimal, RiskLevel, str]:
    if amount >= VERY_LARGE_TXN_THRESHOLD:
        return (
            Decimal("90.00"),
            RiskLevel.high,
            f"amount {amount} exceeds very-large-transaction threshold {VERY_LARGE_TXN_THRESHOLD}",
        )
    if amount >= LARGE_TXN_THRESHOLD:
        return (
            Decimal("55.00"),
            RiskLevel.medium,
            f"amount {amount} exceeds large-transaction threshold {LARGE_TXN_THRESHOLD}",
        )
    return Decimal("5.00"), RiskLevel.low, "amount within normal range"
