import httpx

from app.clients.paypal_auth import PayPalAuth
from shared.errors import DependencyUnavailableError

# Header names PayPal sends on every webhook delivery.
# https://developer.paypal.com/api/rest/webhooks/
TRANSMISSION_ID = "paypal-transmission-id"
TRANSMISSION_TIME = "paypal-transmission-time"
CERT_URL = "paypal-cert-url"
AUTH_ALGO = "paypal-auth-algo"
TRANSMISSION_SIG = "paypal-transmission-sig"

REQUIRED_HEADERS = (TRANSMISSION_ID, TRANSMISSION_TIME, CERT_URL, AUTH_ALGO, TRANSMISSION_SIG)


class WebhookVerificationError(Exception):
    pass


class PayPalWebhookVerifier:
    """Verifies an inbound webhook by asking PayPal's own API to check it
    (POST /v1/notifications/verify-webhook-signature), rather than
    fetching PayPal's certificate and doing signature verification
    locally — the endpoint exists specifically so integrators don't have
    to reimplement PayPal's crypto. `webhook_id` identifies which
    registered webhook (created in the PayPal Developer Dashboard for a
    specific app + set of event types) this delivery claims to be from."""

    def __init__(self, base_url: str, auth: PayPalAuth, webhook_id: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._webhook_id = webhook_id
        self._http = http_client

    async def verify(self, headers: dict[str, str], raw_event: dict) -> None:
        """Raises WebhookVerificationError if the delivery doesn't verify
        (a required header is missing, or PayPal reports FAILURE);
        raises DependencyUnavailableError if PayPal's own API couldn't be
        reached — a network blip here should not be treated as a forged
        webhook."""

        missing = [h for h in REQUIRED_HEADERS if h not in headers]
        if missing:
            raise WebhookVerificationError(f"missing required PayPal headers: {missing}")

        token = await self._auth.get_token()
        try:
            response = await self._http.post(
                f"{self._base_url}/v1/notifications/verify-webhook-signature",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "transmission_id": headers[TRANSMISSION_ID],
                    "transmission_time": headers[TRANSMISSION_TIME],
                    "cert_url": headers[CERT_URL],
                    "auth_algo": headers[AUTH_ALGO],
                    "transmission_sig": headers[TRANSMISSION_SIG],
                    "webhook_id": self._webhook_id,
                    "webhook_event": raw_event,
                },
            )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"PayPal webhook verification unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise DependencyUnavailableError(
                f"PayPal webhook verification call failed: {response.status_code} {response.text[:200]}"
            )

        status = response.json().get("verification_status")
        if status != "SUCCESS":
            raise WebhookVerificationError(f"PayPal reported verification_status={status!r}")
