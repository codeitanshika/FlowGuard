"""API-level tests of the fault-injection control surface (Phase 6) and
its safety property (ADR-0013) against the real running stack."""

import httpx

from tests.live_helpers import (
    FRAUD_URL,
    GATEWAY_URL,
    READONLY_CLIENT_ID,
    READONLY_CLIENT_SECRET,
    auth_headers,
    clear_fault,
    enable_fault,
    login,
)


def test_injected_error_actually_breaks_the_target_then_recovers(merchant_token):
    enable_fault(merchant_token, "fraud", "error_500", error_rate=1.0, duration_seconds=30)
    try:
        broken = httpx.post(f"{FRAUD_URL}/internal/risk-check", json={
            "transaction_id": "00000000-0000-0000-0000-000000000000",
            "user_id": "00000000-0000-0000-0000-000000000000",
            "amount": "1.00", "currency": "USD",
        }, timeout=10.0)
        assert broken.status_code == 500
    finally:
        clear_fault(merchant_token, "fraud")

    recovered = httpx.post(f"{FRAUD_URL}/internal/risk-check", json={
        "transaction_id": "00000000-0000-0000-0000-000000000001",
        "user_id": "00000000-0000-0000-0000-000000000001",
        "amount": "1.00", "currency": "USD",
    }, timeout=10.0)
    assert recovered.status_code == 200


def test_gateway_self_fault_cannot_lock_out_its_own_clear_endpoint(merchant_token):
    """The exact property ADR-0013 exists for: a 100% error rate injected
    into the Gateway itself must never prevent clearing that same fault."""

    enable_fault(merchant_token, "gateway", "error_500", error_rate=1.0, duration_seconds=30)
    try:
        broken = httpx.get(f"{GATEWAY_URL}/api/v1/payments", headers=auth_headers(merchant_token), timeout=10.0)
        assert broken.status_code == 500
    finally:
        clear_fault(merchant_token, "gateway")

    recovered = httpx.get(f"{GATEWAY_URL}/api/v1/payments", headers=auth_headers(merchant_token), timeout=10.0)
    assert recovered.status_code == 200


def test_readonly_client_cannot_configure_faults():
    readonly_token = login(READONLY_CLIENT_ID, READONLY_CLIENT_SECRET)
    response = httpx.post(
        f"{GATEWAY_URL}/api/v1/debug/fault-inject",
        headers=auth_headers(readonly_token),
        json={"target": "gateway", "mode": "error_500"},
        timeout=10.0,
    )
    assert response.status_code == 403
