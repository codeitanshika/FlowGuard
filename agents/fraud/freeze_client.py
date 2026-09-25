import uuid
from typing import Any

import httpx


class FreezeError(Exception):
    """User Service refused or could not perform a freeze."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class FreezeClient:
    """The Fraud Agent's only route to changing user state — it can only
    call this endpoint, which validates the request the same way it would
    validate any other caller (see docs/architecture/03-service-boundaries.md).
    No auth token: internal service-to-service calls have none yet (known
    gap, docs/security/README.md); acceptable for the current
    single-network deployment target."""

    def __init__(self, base_url: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._http = http_client

    async def freeze(
        self, user_id: uuid.UUID, reason: str, risk_assessment_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        body = {"reason": reason, "source": "fraud_agent"}
        if risk_assessment_id is not None:
            body["risk_assessment_id"] = str(risk_assessment_id)
        try:
            response = await self._http.patch(f"{self._base_url}/internal/users/{user_id}/freeze", json=body)
        except httpx.HTTPError as exc:
            raise FreezeError(0, f"User Service unreachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text[:200]
            raise FreezeError(response.status_code, message)
        return response.json()["data"]
