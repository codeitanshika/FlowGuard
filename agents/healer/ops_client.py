from typing import Any

import httpx


class OpsError(Exception):
    """The Ops Controller refused or could not perform a request.
    status is the HTTP status, or 0 when it could not be reached."""

    def __init__(self, status: int, message: str) -> None:
        self.status = status
        super().__init__(message)


class OpsClient:
    """The Healer's only route to changing anything: the Ops Controller,
    authenticated with the token scoped to the Healer."""

    def __init__(self, base_url: str, token: str, http_client: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._http = http_client

    async def _call(self, method: str, path: str, json: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._http.request(method, f"{self._base_url}{path}", headers=self._headers, json=json)
        except httpx.HTTPError as exc:
            raise OpsError(0, f"Ops Controller unreachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                message = response.json()["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = response.text[:200]
            raise OpsError(response.status_code, message)
        return response.json()["data"]

    async def actions(self) -> list[dict[str, Any]]:
        return await self._call("GET", "/ops/actions")

    async def circuits(self) -> dict[str, str]:
        return await self._call("GET", "/ops/circuits")

    async def execute(self, action: str, service: str, dependency: str) -> dict[str, Any]:
        return await self._call("POST", f"/ops/actions/{action}", json={"service": service, "dependency": dependency})
