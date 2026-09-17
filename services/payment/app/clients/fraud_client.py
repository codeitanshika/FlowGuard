import uuid
from decimal import Decimal

import httpx

from app.models.schemas import RiskCheckResult
from shared.errors import DependencyUnavailableError


class FraudClient:
    """Sync call to Fraud Service's /internal/risk-check. Plain httpx with
    a timeout for now — Phase 5 wraps this in a circuit breaker; Phase 4
    adds OTel span propagation. Neither exists yet, so a slow Fraud
    Service currently just makes this call slow, not fail fast."""

    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def check_risk(
        self, transaction_id: uuid.UUID, user_id: uuid.UUID, amount: Decimal, currency: str
    ) -> RiskCheckResult:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/risk-check",
                    json={
                        "transaction_id": str(transaction_id),
                        "user_id": str(user_id),
                        "amount": str(amount),
                        "currency": currency,
                    },
                )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"fraud service unreachable: {exc}") from exc

        if resp.status_code >= 500:
            raise DependencyUnavailableError(f"fraud service error: {resp.status_code}")
        resp.raise_for_status()
        return RiskCheckResult(**resp.json()["data"])
