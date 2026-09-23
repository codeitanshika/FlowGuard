import httpx

from shared.errors import DependencyUnavailableError

_SERVICE_URL_ATTR = {"payment": "payment_service_url"}


class CircuitExecutor:
    """Performs the actual breaker operations. `service` and `dependency`
    reaching here have already passed allowlist validation, and the URL is
    looked up from static settings — nothing in a request can choose where
    this connects."""

    def __init__(self, settings, http_client: httpx.AsyncClient) -> None:
        self._settings = settings
        self._http = http_client

    def _base_url(self, service: str) -> str:
        return getattr(self._settings, _SERVICE_URL_ATTR[service]).rstrip("/")

    async def set_circuit(self, action: str, service: str, dependency: str) -> str:
        verb = "open" if action == "open-circuit" else "reset"
        url = f"{self._base_url(service)}/internal/circuit-breakers/{dependency}/{verb}"
        try:
            response = await self._http.post(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"{service} rejected or did not answer: {exc}") from exc
        return str(response.json().get("data", {}).get("state", "unknown"))

    async def circuit_states(self, service: str) -> dict[str, str]:
        try:
            response = await self._http.get(f"{self._base_url(service)}/internal/circuit-breakers")
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(f"could not read {service} breaker state: {exc}") from exc
        return dict(response.json().get("data") or {})
