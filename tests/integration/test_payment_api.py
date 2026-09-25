"""API-level tests against the real, running stack (Gateway -> Payment ->
Fraud/User/Provider), no mocks. Run with `docker compose up --build -d`
already running: `python -m pytest tests/integration -v`."""

from decimal import Decimal

import httpx

from tests.live_helpers import GATEWAY_URL, auth_headers, create_funded_user, get_user, make_payment


def test_happy_path_payment_completes_and_debits_the_balance(merchant_token):
    user_id = create_funded_user(merchant_token, "100.00")

    response = make_payment(merchant_token, user_id, "10.00")

    assert response.status_code == 201
    body = response.json()["data"]
    assert body["status"] == "completed"
    assert body["provider"] == "mock"
    assert body["provider_reference"] is not None

    balance = get_user(merchant_token, user_id)["balance"]
    assert Decimal(balance) == Decimal("90.00")


def test_idempotency_key_replay_returns_the_same_result_without_double_debit(merchant_token):
    user_id = create_funded_user(merchant_token, "100.00")
    key = "replay-test-key"

    first = make_payment(merchant_token, user_id, "10.00", idempotency_key=key)
    second = make_payment(merchant_token, user_id, "10.00", idempotency_key=key)

    assert first.status_code == 201 and second.status_code == 201
    assert first.json()["data"]["id"] == second.json()["data"]["id"]
    balance = get_user(merchant_token, user_id)["balance"]
    assert Decimal(balance) == Decimal("90.00"), "a replayed request must never debit twice"


def test_same_idempotency_key_with_a_different_body_is_rejected(merchant_token):
    user_id = create_funded_user(merchant_token, "100.00")
    key = "conflict-test-key"

    make_payment(merchant_token, user_id, "10.00", idempotency_key=key)
    conflicting = make_payment(merchant_token, user_id, "20.00", idempotency_key=key)

    assert conflicting.status_code == 409


def test_provider_decline_fails_the_payment_and_leaves_balance_untouched(merchant_token):
    # MockPaymentProvider's deterministic decline trigger — see
    # services/payment/app/clients/provider_client.py.
    user_id = create_funded_user(merchant_token, "100.00")

    response = make_payment(merchant_token, user_id, "0.13")

    assert response.status_code == 201  # recorded outcome, not a transport error
    body = response.json()["data"]
    assert body["status"] == "failed" and body["failure_reason"]
    balance = get_user(merchant_token, user_id)["balance"]
    assert Decimal(balance) == Decimal("100.00"), "a declined capture must be compensated back to the user"


def test_insufficient_balance_is_a_conflict(merchant_token):
    user_id = create_funded_user(merchant_token, "5.00")

    response = make_payment(merchant_token, user_id, "10.00")

    body = response.json()["data"]
    assert response.status_code == 201 and body["status"] == "failed"
    assert "balance" in body["failure_reason"].lower()


def test_missing_idempotency_key_is_rejected(merchant_token):
    user_id = create_funded_user(merchant_token, "100.00")

    response = httpx.post(
        f"{GATEWAY_URL}/api/v1/payments",
        headers=auth_headers(merchant_token),
        json={"user_id": user_id, "amount": "1.00", "currency": "USD"},
        timeout=10.0,
    )
    assert response.status_code in (400, 422)


def test_get_and_list_payments_reflect_what_was_created(merchant_token):
    user_id = create_funded_user(merchant_token, "50.00")
    created = make_payment(merchant_token, user_id, "5.00").json()["data"]

    fetched = httpx.get(
        f"{GATEWAY_URL}/api/v1/payments/{created['id']}", headers=auth_headers(merchant_token), timeout=10.0
    )
    assert fetched.status_code == 200 and fetched.json()["data"]["id"] == created["id"]

    listed = httpx.get(
        f"{GATEWAY_URL}/api/v1/payments", params={"user_id": user_id}, headers=auth_headers(merchant_token), timeout=10.0
    )
    assert listed.status_code == 200
    assert any(p["id"] == created["id"] for p in listed.json()["data"])
