from agents.fraud.schemas import RiskDecision
from agents.fraud.thresholds import VelocityThresholds


def decide(velocity_count: int, geo_anomaly: bool, thresholds: VelocityThresholds) -> RiskDecision:
    """The only function that decides freeze/no-freeze/review — deliberately
    the single choke point ADR-0007 requires. Nothing downstream (not the
    LLM, not agent.py) can change `level` once this returns."""

    reasons = []
    if velocity_count >= thresholds.high:
        reasons.append(
            f"{velocity_count} transactions in the last {thresholds.window_seconds}s "
            f"(threshold {thresholds.high})"
        )
    elif velocity_count >= thresholds.borderline:
        reasons.append(
            f"{velocity_count} transactions in the last {thresholds.window_seconds}s "
            f"is elevated (borderline threshold {thresholds.borderline}, freeze threshold {thresholds.high})"
        )
    if geo_anomaly:
        reasons.append("transaction originated from a country different from the user's usual one")

    if velocity_count >= thresholds.high:
        level = "high"
    elif velocity_count >= thresholds.borderline or geo_anomaly:
        level = "borderline"
    else:
        level = "low"
        reasons = ["velocity and geo signals within normal range"]

    return RiskDecision(level=level, velocity_count=velocity_count, geo_anomaly=geo_anomaly, reasons=reasons)
