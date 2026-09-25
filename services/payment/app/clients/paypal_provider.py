import uuid
from decimal import Decimal

import httpx

from app.clients.paypal_auth import PayPalAuth
from app.clients.paypal_errors import PayPalDecline, classify
from app.clients.provider_client import ProviderResult
from shared.errors import DependencyUnavailableError
from shared.fault_injection import FaultInjected, FaultInjector
from shared.logging import get_logger

logger = get_logger(__name__)


class PayPalProvider:
    """Real integration against PayPal's Orders v2 API (sandbox by default
    — see ADR-0017), behind the same PaymentProvider interface
    MockPaymentProvider implements, so Payment Service's orchestration
    logic doesn't know or care which one is active
    (docs/architecture/03-service-boundaries.md's swappable-abstraction
    goal, FR13).

    capture() creates an order and immediately attempts to capture it.
    PayPal's documented behavior for a purely server-to-server order with
    no buyer-approval redirect (FlowGuard's Payment Service has no
    redirect step anywhere in its API) is a real, specific decline —
    422 UNPROCESSABLE_ENTITY, issue ORDER_NOT_APPROVED — which this
    treats as a genuine provider decline (ProviderResult.success=False),
    not an outage: PayPal understood the request perfectly and correctly
    said no. See ADR-0017 for why this is the honest scope for this
    phase rather than building a buyer-redirect flow FlowGuard's
    architecture doesn't have anywhere else."""

    name = "paypal"

    def __init__(
        self, base_url: str, auth: PayPalAuth, http_client: httpx.AsyncClient, fault_injector: FaultInjector
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._auth = auth
        self._http = http_client
        self._fault_injector = fault_injector

    async def capture(self, transaction_id: uuid.UUID, amount: Decimal, currency: str) -> ProviderResult:
        try:
            await self._fault_injector.maybe_apply()
        except FaultInjected as exc:
            raise DependencyUnavailableError(f"payment provider fault injected: {exc}") from exc

        order_id: str | None = None
        try:
            order_id = await self._create_order(transaction_id, amount, currency)
            capture_id = await self._capture_order(order_id)
        except PayPalDecline as decline:
            # order_id stays None if _create_order itself declined (e.g.
            # AMOUNT_MISMATCH) — there's no order to reference in that case.
            logger.info(
                "paypal.capture_declined", order_id=order_id, issue=decline.issue, transaction_id=str(transaction_id)
            )
            return ProviderResult(success=False, provider_reference=order_id, failure_reason=decline.reason)

        return ProviderResult(success=True, provider_reference=capture_id, failure_reason=None)

    async def _create_order(self, transaction_id: uuid.UUID, amount: Decimal, currency: str) -> str:
        token = await self._auth.get_token()
        try:
            response = await self._http.post(
                f"{self._base_url}/v2/checkout/orders",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "intent": "CAPTURE",
                    "purchase_units": [
                        {
                            "reference_id": str(transaction_id),
                            "amount": {"currency_code": currency, "value": str(amount)},
                        }
                    ],
                },
            )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"PayPal create-order unreachable: {exc}") from exc

        if response.status_code >= 400:
            decline = classify(response)
            if decline:
                # A create-order decline (e.g. AMOUNT_MISMATCH) still means
                # there's no order to reference — surface it the same way a
                # capture decline would, with no provider_reference.
                raise decline
            raise DependencyUnavailableError(
                f"PayPal create-order failed: {response.status_code} {response.text[:200]}"
            )
        return response.json()["id"]

    async def _capture_order(self, order_id: str) -> str:
        token = await self._auth.get_token()
        try:
            response = await self._http.post(
                f"{self._base_url}/v2/checkout/orders/{order_id}/capture",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"PayPal capture unreachable: {exc}") from exc

        if response.status_code >= 400:
            decline = classify(response)
            if decline:
                raise decline
            raise DependencyUnavailableError(f"PayPal capture failed: {response.status_code} {response.text[:200]}")

        body = response.json()
        captures = body["purchase_units"][0]["payments"]["captures"]
        return captures[0]["id"]

    async def get_capture_status(self, capture_id: str) -> dict:
        """Status lookup (FR13) — exposed via /internal/providers/paypal
        for operational/diagnostic use, not on the synchronous capture
        path. https://developer.paypal.com/docs/api/payments/v2/#captures_get"""
        token = await self._auth.get_token()
        try:
            response = await self._http.get(
                f"{self._base_url}/v2/payments/captures/{capture_id}",
                headers={"Authorization": f"Bearer {token}"},
            )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"PayPal status lookup unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise DependencyUnavailableError(
                f"PayPal status lookup failed: {response.status_code} {response.text[:200]}"
            )
        return response.json()
