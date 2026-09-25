import time

import httpx

from shared.errors import DependencyUnavailableError


class PayPalAuth:
    """OAuth2 client-credentials token, cached in-process. Single Payment
    Service instance in this deployment, so an in-memory cache (not Redis)
    is enough — see ADR-0017. Refreshed a safety margin before its real
    expiry rather than on a 401, so a capture call never loses time to a
    mid-request re-auth."""

    _REFRESH_MARGIN_SECONDS = 60

    def __init__(self, base_url: str, client_id: str, client_secret: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http_client
        self._token: str | None = None
        self._expires_at: float = 0.0

    async def get_token(self) -> str:
        if self._token is not None and time.monotonic() < self._expires_at:
            return self._token

        try:
            response = await self._http.post(
                f"{self._base_url}/v1/oauth2/token",
                auth=(self._client_id, self._client_secret),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials"},
            )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"PayPal auth unreachable: {exc}") from exc
        if response.status_code >= 400:
            raise DependencyUnavailableError(f"PayPal auth failed: {response.status_code} {response.text[:200]}")

        payload = response.json()
        self._token = payload["access_token"]
        self._expires_at = time.monotonic() + max(payload["expires_in"] - self._REFRESH_MARGIN_SECONDS, 0)
        return self._token
