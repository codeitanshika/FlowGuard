"""The Ops Controller's allowlist — code, not configuration (ADR-0005).

Adding a capability means editing this file in a reviewed commit; nothing
at runtime, and nothing an LLM emits, can extend it. Pure data with no
dependencies so the Healer can import the same definition for its own
pre-flight validation (the Ops Controller still re-validates: it is the
enforcement point, the Healer's check is a courtesy).

Deliberately absent for now: `restart-service` (needs Docker-socket access,
a much larger blast radius) and `shed-traffic` (needs Gateway support that
does not exist). Both remain in ADR-0005's eventual list."""

from dataclasses import dataclass
from typing import Mapping

from pydantic import BaseModel, ConfigDict


class ActionRejected(Exception):
    pass


class CircuitParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    dependency: str


@dataclass(frozen=True)
class ActionSpec:
    name: str
    description: str
    # service -> the dependencies of that service this action may touch.
    targets: Mapping[str, frozenset[str]]


_PAYMENT_BREAKERS = {"payment": frozenset({"fraud", "user", "provider"})}

ALLOWLIST: Mapping[str, ActionSpec] = {
    "open-circuit": ActionSpec(
        name="open-circuit",
        description="Force a circuit breaker OPEN so calls to a failing dependency fail fast. "
        "Self-heals: the breaker probes again after its recovery timeout.",
        targets=_PAYMENT_BREAKERS,
    ),
    "reset-circuit": ActionSpec(
        name="reset-circuit",
        description="Force a circuit breaker CLOSED so traffic to the dependency resumes.",
        targets=_PAYMENT_BREAKERS,
    ),
}


def validate_action(action: str, raw_params: object) -> CircuitParams:
    """Returns validated params or raises ActionRejected. Unknown action,
    malformed/extra parameters, and out-of-scope targets are all rejected."""

    spec = ALLOWLIST.get(action)
    if spec is None:
        raise ActionRejected(f"action '{action}' is not on the allowlist")
    try:
        params = CircuitParams.model_validate(raw_params)
    except ValueError as exc:
        raise ActionRejected(f"invalid parameters: {exc}") from exc
    if params.dependency not in spec.targets.get(params.service, frozenset()):
        raise ActionRejected(
            f"'{action}' is not permitted on service='{params.service}' dependency='{params.dependency}'"
        )
    return params


def describe_allowlist() -> list[dict]:
    return [
        {
            "action": spec.name,
            "description": spec.description,
            "params": {"service": "string", "dependency": "string"},
            "targets": {svc: sorted(deps) for svc, deps in spec.targets.items()},
        }
        for spec in ALLOWLIST.values()
    ]
