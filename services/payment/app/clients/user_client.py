import uuid
from decimal import Decimal

import httpx

from shared.errors import ConflictError, DependencyUnavailableError, NotFoundError


class UserClient:
    """Sync calls to User Service's /internal/users/{id}/debit and
    /credit. Breaker-wrapped by the orchestrator (Phase 5), not here —
    see FraudClient for why, and ADR-0012 for why timeout=2.0 not 3.0."""

    def __init__(self, base_url: str, timeout: float = 2.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout

    async def debit(self, user_id: uuid.UUID, amount: Decimal, currency: str, transaction_id: uuid.UUID) -> Decimal:
        return await self._adjust("debit", user_id, amount, currency, transaction_id)

    async def credit(self, user_id: uuid.UUID, amount: Decimal, currency: str, transaction_id: uuid.UUID) -> Decimal:
        return await self._adjust("credit", user_id, amount, currency, transaction_id)

    async def _adjust(
        self, action: str, user_id: uuid.UUID, amount: Decimal, currency: str, transaction_id: uuid.UUID
    ) -> Decimal:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/internal/users/{user_id}/{action}",
                    json={
                        "amount": str(amount),
                        "currency": currency,
                        "transaction_id": str(transaction_id),
                    },
                )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"user service unreachable: {exc}") from exc

        if resp.status_code == 404:
            raise NotFoundError(f"user {user_id} not found")
        if resp.status_code == 409:
            raise ConflictError(_error_message(resp))
        if resp.status_code >= 500:
            raise DependencyUnavailableError(f"user service error: {resp.status_code}")
        resp.raise_for_status()
        return Decimal(resp.json()["data"]["balance"])


def _error_message(resp: httpx.Response) -> str:
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError):
        return f"user service returned {resp.status_code}"
