"""API-level test of the full Fraud Agent freeze flow (Phase 9) against the
real running stack — the automated version of the manual verification
done live in that phase (docs/decisions/ADR-0016.md)."""

import time

from tests.live_helpers import create_funded_user, get_user, make_payment


def test_a_burst_of_payments_freezes_the_user_and_blocks_further_payments(merchant_token):
    user_id = create_funded_user(merchant_token, "1000.00")

    # Default velocity threshold is 10 in 180s (agents/fraud/thresholds.py)
    # — 12 back-to-back payments reliably crosses it inside the window.
    for i in range(12):
        make_payment(merchant_token, user_id, "1.00", idempotency_key=f"freeze-test-{user_id}-{i}")

    # The Fraud Agent consumes payment.created asynchronously — give it a
    # moment to catch up rather than asserting on the freeze instantly.
    frozen = False
    for _ in range(15):
        if get_user(merchant_token, user_id)["status"] == "frozen":
            frozen = True
            break
        time.sleep(1)
    assert frozen, "user was not frozen within 15s of a velocity-threshold-crossing burst"

    blocked = make_payment(merchant_token, user_id, "1.00", idempotency_key=f"freeze-test-{user_id}-after")
    body = blocked.json()["data"]
    assert blocked.status_code == 201 and body["status"] == "failed"
    assert "not active" in body["failure_reason"].lower()
