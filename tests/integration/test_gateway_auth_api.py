"""API-level tests of the Gateway's authentication/authorization boundary
against the real running stack — see docs/security/README.md."""

import httpx

from tests.live_helpers import (
    GATEWAY_URL,
    READONLY_CLIENT_ID,
    READONLY_CLIENT_SECRET,
    auth_headers,
    create_funded_user,
    login,
    make_payment,
)


def test_no_token_is_unauthorized():
    response = httpx.post(
        f"{GATEWAY_URL}/api/v1/payments",
        json={"user_id": "x", "amount": "1.00", "currency": "USD"},
        headers={"Idempotency-Key": "no-token-test", "Content-Type": "application/json"},
        timeout=10.0,
    )
    assert response.status_code == 401


def test_malformed_token_is_unauthorized():
    response = httpx.get(
        f"{GATEWAY_URL}/api/v1/payments", headers={"Authorization": "Bearer not-a-real-token"}, timeout=10.0
    )
    assert response.status_code == 401


def test_wrong_credentials_cannot_log_in():
    response = httpx.post(
        f"{GATEWAY_URL}/auth/login",
        json={"client_id": "test-merchant", "client_secret": "definitely-wrong"},
        timeout=10.0,
    )
    assert response.status_code == 401


def test_valid_token_missing_write_scope_is_forbidden(merchant_token):
    readonly_token = login(READONLY_CLIENT_ID, READONLY_CLIENT_SECRET)
    user_id = create_funded_user(merchant_token, "10.00")  # created with the merchant, who can write

    response = make_payment(readonly_token, user_id, "1.00")

    assert response.status_code == 403


def test_valid_token_with_required_scope_succeeds(merchant_token):
    user_id = create_funded_user(merchant_token, "10.00")

    response = make_payment(merchant_token, user_id, "1.00")

    assert response.status_code == 201


def test_internal_routes_are_not_reachable_through_the_gateway(merchant_token):
    # /internal/* must 404 through the Gateway regardless of auth — see
    # docs/architecture/03-service-boundaries.md.
    response = httpx.patch(
        f"{GATEWAY_URL}/api/v1/internal/users/x/freeze",
        headers=auth_headers(merchant_token),
        json={"reason": "x", "source": "manual"},
        timeout=10.0,
    )
    assert response.status_code == 404
