from dataclasses import dataclass
from typing import Literal

from agents.healer.schemas import HealerDecision
from agents.ops_controller.allowlist import ActionRejected, validate_action

PlanKind = Literal["execute", "noop", "escalate"]

_EXECUTE_CONFIDENCE = {"medium", "high"}


@dataclass(frozen=True)
class Plan:
    kind: PlanKind
    reason: str
    # False only when the decision itself was malformed or off the
    # allowlist — recorded in agent_decisions.validated.
    validated: bool = True
    action: str | None = None
    service: str | None = None
    dependency: str | None = None


def plan(decision: HealerDecision, circuits: dict[str, str] | None) -> Plan:
    """Turns a decision into something the Healer is willing to send to the
    Ops Controller — or into an escalation. This is a pre-flight check for
    fast, well-explained failures; the Ops Controller re-validates the
    same rules and is the actual enforcement point (ADR-0005)."""

    if decision.action == "escalate":
        return Plan("escalate", "decision is to escalate for human review")

    if decision.confidence not in _EXECUTE_CONFIDENCE:
        return Plan("escalate", f"confidence '{decision.confidence}' is too low to act automatically")

    try:
        params = validate_action(decision.action, {"service": decision.service, "dependency": decision.dependency})
    except ActionRejected as exc:
        return Plan("escalate", f"rejected by the allowlist: {exc}", validated=False)

    state = (circuits or {}).get(params.dependency)
    if decision.action == "open-circuit" and state == "open":
        return Plan("noop", "breaker already open", True, decision.action, params.service, params.dependency)
    if decision.action == "reset-circuit" and state == "closed":
        return Plan("noop", "breaker already closed", True, decision.action, params.service, params.dependency)

    return Plan("execute", "validated", True, decision.action, params.service, params.dependency)
