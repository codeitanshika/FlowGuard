"""Unit tests for the PayPal integration (Phase 10) — real request shapes
against PayPal's Orders v2 / webhooks APIs (verified live against PayPal's
docs while building this), exercised over httpx.MockTransport so no
network or real credentials are needed. Run: cd services/payment &&
python -m pytest tests/unit/test_paypal_provider.py -v"""

import json
import time
import uuid
from decimal import Decimal

import httpx
import pytest

from app.clients.paypal_auth import PayPalAuth
from app.clients.paypal_errors import PayPalDecline, classify, parse_error
from app.clients.paypal_provider import PayPalProvider
from app.clients.paypal_webhooks import PayPalWebhookVerifier, WebhookVerificationError
from shared.errors import DependencyUnavailableError
from shared.fault_injection import FaultInjected, FaultInjector


def client_for(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


BASE = "https://api-m.sandbox.paypal.com"


def token_response(access_token="tok-1", expires_in=3600):
    return httpx.Response(200, json={"access_token": access_token, "token_type": "Bearer", "expires_in": expires_in})


class NeverFaults:
    async def maybe_apply(self) -> None:
        return None


class AlwaysFaults:
    async def maybe_apply(self) -> None:
        raise FaultInjected("injected error_500")


# --- PayPalAuth ----------------------------------------------------------

async def test_auth_fetches_and_caches_the_token():
    calls = []

    def handler(request):
        calls.append(request)
        assert request.url.path == "/v1/oauth2/token"
        assert request.headers["authorization"].startswith("Basic ")
        assert "grant_type=client_credentials" in request.content.decode()
        return token_response()

    auth = PayPalAuth(BASE, "id", "secret", client_for(handler))
    assert await auth.get_token() == "tok-1"
    assert await auth.get_token() == "tok-1"
    assert len(calls) == 1, "a cached, unexpired token must not trigger a second request"


async def test_auth_refetches_after_expiry():
    tokens = iter(["tok-1", "tok-2"])

    def handler(request):
        return token_response(next(tokens), expires_in=61)  # just above the 60s refresh margin

    auth = PayPalAuth(BASE, "id", "secret", client_for(handler))
    assert await auth.get_token() == "tok-1"
    auth._expires_at = time.monotonic() - 1  # force expiry without a real sleep
    assert await auth.get_token() == "tok-2"


async def test_auth_failure_is_a_dependency_error():
    auth = PayPalAuth(BASE, "bad", "creds", client_for(lambda r: httpx.Response(401, json={"error": "invalid_client"})))
    with pytest.raises(DependencyUnavailableError):
        await auth.get_token()


async def test_auth_network_error_is_a_dependency_error():
    def handler(request):
        raise httpx.ConnectError("refused")

    auth = PayPalAuth(BASE, "id", "secret", client_for(handler))
    with pytest.raises(DependencyUnavailableError):
        await auth.get_token()


# --- error classification -------------------------------------------------

def paypal_error(issue, description="declined", status=422):
    return httpx.Response(
        status,
        json={"name": "UNPROCESSABLE_ENTITY", "message": "x", "details": [{"issue": issue, "description": description}]},
    )


@pytest.mark.parametrize("issue", [
    "ORDER_NOT_APPROVED", "INSTRUMENT_DECLINED", "PAYER_ACTION_REQUIRED",
    "TRANSACTION_REFUSED", "DUPLICATE_INVOICE_ID", "AMOUNT_MISMATCH",
])
def test_known_decline_issues_classify_as_a_decline(issue):
    decline = classify(paypal_error(issue))
    assert isinstance(decline, PayPalDecline) and decline.issue == issue


@pytest.mark.parametrize("response", [
    paypal_error("SOME_UNKNOWN_FUTURE_ISSUE"),
    httpx.Response(500, text="internal server error"),
    httpx.Response(429, json={"name": "RATE_LIMIT_REACHED"}),
    httpx.Response(401, text="not json"),
])
def test_unrecognized_or_infra_errors_are_not_declines(response):
    assert classify(response) is None


def test_parse_error_falls_back_gracefully_on_unparseable_body():
    issue, message = parse_error(httpx.Response(500, text="<html>gateway timeout</html>"))
    assert issue is None and "500" in message


# --- PayPalProvider.capture -----------------------------------------------

def order_created(order_id="order-1"):
    return httpx.Response(201, json={"id": order_id, "status": "CREATED"})


def order_captured(capture_id="capture-1"):
    return httpx.Response(
        201,
        json={"id": "order-1", "status": "COMPLETED",
              "purchase_units": [{"payments": {"captures": [{"id": capture_id, "status": "COMPLETED"}]}}]},
    )


def make_provider(handler, fault_injector=None) -> PayPalProvider:
    http_client = client_for(handler)
    auth = PayPalAuth(BASE, "id", "secret", http_client)
    return PayPalProvider(BASE, auth, http_client, fault_injector or NeverFaults())


async def test_capture_happy_path_creates_then_captures_and_returns_the_capture_id():
    seen = []

    def handler(request):
        seen.append(request.url.path)
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        if request.url.path == "/v2/checkout/orders":
            body = json.loads(request.content)
            assert body["intent"] == "CAPTURE"
            unit = body["purchase_units"][0]
            assert unit["amount"] == {"currency_code": "USD", "value": "42.50"}
            return order_created()
        if request.url.path == "/v2/checkout/orders/order-1/capture":
            assert request.headers["authorization"] == "Bearer tok-1"
            return order_captured()
        raise AssertionError(f"unexpected path {request.url.path}")

    result = await make_provider(handler).capture(uuid.uuid4(), Decimal("42.50"), "USD")
    assert result.success is True and result.provider_reference == "capture-1"
    assert seen == ["/v1/oauth2/token", "/v2/checkout/orders", "/v2/checkout/orders/order-1/capture"]


async def test_capture_not_approved_is_a_decline_not_an_outage():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        if request.url.path == "/v2/checkout/orders":
            return order_created()
        return paypal_error("ORDER_NOT_APPROVED", "Payer has not yet approved order")

    result = await make_provider(handler).capture(uuid.uuid4(), Decimal("10.00"), "USD")
    assert result.success is False
    assert result.provider_reference == "order-1"  # the order still exists, just uncaptured
    assert "approve" in result.failure_reason.lower()


async def test_create_order_decline_has_no_provider_reference():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        return paypal_error("AMOUNT_MISMATCH", "amount does not match")

    result = await make_provider(handler).capture(uuid.uuid4(), Decimal("10.00"), "USD")
    assert result.success is False and result.provider_reference is None


@pytest.mark.parametrize("failing_step", ["create", "capture"])
async def test_infra_failures_raise_dependency_unavailable_not_a_decline(failing_step):
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        if request.url.path == "/v2/checkout/orders":
            if failing_step == "create":
                return httpx.Response(500, text="internal error")
            return order_created()
        return httpx.Response(503, text="service unavailable")

    with pytest.raises(DependencyUnavailableError):
        await make_provider(handler).capture(uuid.uuid4(), Decimal("10.00"), "USD")


async def test_network_error_during_capture_raises_dependency_unavailable():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        if request.url.path == "/v2/checkout/orders":
            return order_created()
        raise httpx.ConnectTimeout("timed out")

    with pytest.raises(DependencyUnavailableError):
        await make_provider(handler).capture(uuid.uuid4(), Decimal("10.00"), "USD")


async def test_injected_fault_is_checked_before_any_real_http_call():
    def handler(request):
        raise AssertionError("must not reach PayPal when a fault is injected")

    with pytest.raises(DependencyUnavailableError, match="fault injected"):
        await make_provider(handler, AlwaysFaults()).capture(uuid.uuid4(), Decimal("10.00"), "USD")


# --- status lookup ---------------------------------------------------------

async def test_get_capture_status_returns_the_paypal_response():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        assert request.url.path == "/v2/payments/captures/capture-1"
        return httpx.Response(200, json={"id": "capture-1", "status": "COMPLETED"})

    status = await make_provider(handler).get_capture_status("capture-1")
    assert status["status"] == "COMPLETED"


async def test_get_capture_status_failure_is_a_dependency_error():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND"})

    with pytest.raises(DependencyUnavailableError):
        await make_provider(handler).get_capture_status("nope")


# --- webhook verification --------------------------------------------------

GOOD_HEADERS = {
    "paypal-transmission-id": "t1",
    "paypal-transmission-time": "2026-01-01T00:00:00Z",
    "paypal-cert-url": "https://api.paypal.com/cert.pem",
    "paypal-auth-algo": "SHA256withRSA",
    "paypal-transmission-sig": "sig==",
}


def make_verifier(handler) -> PayPalWebhookVerifier:
    http_client = client_for(handler)
    auth = PayPalAuth(BASE, "id", "secret", http_client)
    return PayPalWebhookVerifier(BASE, auth, "WH-123", http_client)


async def test_webhook_verification_success():
    def handler(request):
        if request.url.path == "/v1/oauth2/token":
            return token_response()
        assert request.url.path == "/v1/notifications/verify-webhook-signature"
        body = json.loads(request.content)
        assert body["webhook_id"] == "WH-123"
        assert body["transmission_id"] == "t1"
        return httpx.Response(200, json={"verification_status": "SUCCESS"})

    await make_verifier(handler).verify(GOOD_HEADERS, {"event_type": "PAYMENT.CAPTURE.COMPLETED"})


async def test_webhook_verification_failure_is_rejected():
    verifier = make_verifier(lambda r: token_response() if r.url.path == "/v1/oauth2/token"
                              else httpx.Response(200, json={"verification_status": "FAILURE"}))
    with pytest.raises(WebhookVerificationError):
        await verifier.verify(GOOD_HEADERS, {"event_type": "x"})


async def test_webhook_missing_headers_is_rejected_without_calling_paypal():
    def handler(request):
        raise AssertionError("must not call PayPal when required headers are missing")

    verifier = make_verifier(handler)
    with pytest.raises(WebhookVerificationError, match="missing"):
        await verifier.verify({"paypal-transmission-id": "t1"}, {"event_type": "x"})


async def test_webhook_paypal_api_outage_is_a_dependency_error_not_a_rejection():
    verifier = make_verifier(lambda r: token_response() if r.url.path == "/v1/oauth2/token"
                              else httpx.Response(500, text="down"))
    with pytest.raises(DependencyUnavailableError):
        await verifier.verify(GOOD_HEADERS, {"event_type": "x"})
