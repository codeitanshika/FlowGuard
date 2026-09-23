from agents.healer.context import SpanFact, implicated_dependencies
from agents.healer.schemas import AnomalyEvent, HealerDecision

# Services whose own failures are guarded by a Payment-side breaker.
_BREAKER_GUARDED = {"user": "user", "fraud": "fraud"}


def _escalate(root_cause: str, reasoning: str) -> HealerDecision:
    return HealerDecision(root_cause=root_cause, confidence="low", action="escalate", reasoning=reasoning)


def decide(anomaly: AnomalyEvent, facts: list[SpanFact]) -> HealerDecision:
    """Deterministic fallback for well-known signatures, used whenever the
    LLM is unavailable, refuses, or returns something invalid (failure
    scenario 8) — the AI layer must never be a dependency of resilience.
    Deliberately narrow: only error-rate anomalies with unambiguous
    evidence get an action; everything else is escalated to a human rather
    than guessed at."""

    if anomaly.metric != "error_rate":
        return _escalate(
            f"{anomaly.metric} anomaly on {anomaly.service}",
            f"no automated rule for {anomaly.metric}; needs a human or the LLM",
        )

    if anomaly.service in _BREAKER_GUARDED:
        dependency = _BREAKER_GUARDED[anomaly.service]
        return HealerDecision(
            root_cause=f"{anomaly.service} service is returning errors "
            f"(error rate {anomaly.observed_value:.0%})",
            confidence="high",
            action="open-circuit",
            service="payment",
            dependency=dependency,
            reasoning=f"Payment's '{dependency}' breaker guards calls to the failing {anomaly.service} "
            "service; opening it makes those calls fail fast instead of piling onto a failing service.",
        )

    if anomaly.service == "payment":
        dependencies = implicated_dependencies(facts, "payment")
        if len(dependencies) == 1:
            dependency = next(iter(dependencies))
            return HealerDecision(
                root_cause=f"payment failures are caused by its '{dependency}' dependency",
                confidence="high",
                action="open-circuit",
                service="payment",
                dependency=dependency,
                reasoning=f"Payment's failing request spans name only the '{dependency}' dependency.",
            )
        if not dependencies:
            return _escalate(
                "payment errors with no identifiable failing dependency",
                "the example trace named no dependency (or was unavailable); not guessing",
            )
        return _escalate(
            "payment errors implicate more than one dependency",
            f"ambiguous evidence: {sorted(dependencies)}",
        )

    return _escalate(
        f"error-rate anomaly on {anomaly.service}",
        f"{anomaly.service} has no circuit breaker the Healer is allowed to act on",
    )
