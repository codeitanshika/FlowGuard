"""The Ops Controller allowlist is the safety boundary from ADR-0005, so
these tests are adversarial: everything an LLM (or an attacker steering
one) might emit that is not exactly an allowed action must be rejected."""

import pytest

from agents.ops_controller.allowlist import (
    ALLOWLIST,
    ActionRejected,
    describe_allowlist,
    validate_action,
)


@pytest.mark.parametrize("action", ["open-circuit", "reset-circuit"])
@pytest.mark.parametrize("dependency", ["fraud", "user", "provider"])
def test_every_allowed_action_and_target_is_accepted(action, dependency):
    params = validate_action(action, {"service": "payment", "dependency": dependency})
    assert (params.service, params.dependency) == ("payment", dependency)


@pytest.mark.parametrize(
    "action",
    [
        "restart-service",  # deliberately not on the allowlist yet
        "shed-traffic",
        "docker compose restart payment",
        "rm -rf /",
        "OPEN-CIRCUIT",  # case-sensitive
        "open-circuit ",
        "",
    ],
)
def test_actions_outside_the_allowlist_are_rejected(action):
    with pytest.raises(ActionRejected):
        validate_action(action, {"service": "payment", "dependency": "provider"})


@pytest.mark.parametrize(
    "params",
    [
        {"service": "user", "dependency": "provider"},  # only payment has breakers
        {"service": "gateway", "dependency": "fraud"},
        {"service": "payment", "dependency": "database"},
        {"service": "payment", "dependency": "../../etc/passwd"},
        {"service": "payment", "dependency": "provider/open?x=1"},
        {"service": "payment", "dependency": "provider ; rm -rf /"},
        {"service": "http://evil.example", "dependency": "provider"},
        {"service": "payment"},  # missing field
        {"dependency": "provider"},
        {},
        {"service": "payment", "dependency": "provider", "url": "http://evil.example"},  # extra field
        {"service": "payment", "dependency": "provider", "command": "restart"},
        {"service": 1, "dependency": ["provider"]},
        "payment:provider",
        None,
        ["payment", "provider"],
    ],
)
def test_malformed_or_out_of_scope_params_are_rejected(params):
    with pytest.raises(ActionRejected):
        validate_action("open-circuit", params)


def test_allowlist_contains_exactly_the_reviewed_actions():
    # If this fails, someone added a capability: make sure that was a
    # deliberate, reviewed decision (ADR-0005), then update this test.
    assert set(ALLOWLIST) == {"open-circuit", "reset-circuit"}
    assert {a["action"] for a in describe_allowlist()} == {"open-circuit", "reset-circuit"}


def test_every_target_is_a_payment_breaker():
    for spec in ALLOWLIST.values():
        assert dict(spec.targets) == {"payment": frozenset({"fraud", "user", "provider"})}
