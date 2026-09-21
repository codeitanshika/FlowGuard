import httpx
from fastapi import APIRouter, Depends

from app.api.authorization import check_scope, required_scope
from app.api.security_deps import get_current_client
from app.core.config import Settings, get_settings
from app.core.dependencies import get_fault_injector_self
from app.models.schemas import AuthenticatedClient, DebugFaultInjectRequest
from shared.errors import DependencyUnavailableError, NotFoundError
from shared.fault_injection import FaultInjectionRequest, FaultInjector
from shared.schemas import Envelope

# Registered in main.py *before* the catch-all proxy router
# (build_proxy_router matches /api/v1/{full_path:path}), or every
# request here would be swallowed by the proxy and 404 against the
# route table instead of reaching this router.
router = APIRouter(prefix="/api/v1/debug", tags=["debug"])

# "payment-provider" isn't its own network target — it's Payment
# Service's in-process MockPaymentProvider, reached the same way as
# "payment" but with component="provider" instead of "self" (see
# services/payment/app/clients/provider_client.py). Every other target
# maps straight to that service's own "self" FaultInjector.
_FORWARD_TARGETS = {
    "payment": ("payment_service_url", "self"),
    "fraud": ("fraud_service_url", "self"),
    "user": ("user_service_url", "self"),
    "notification": ("notification_service_url", "self"),
    "payment-provider": ("payment_service_url", "provider"),
}


@router.post("/fault-inject", response_model=Envelope[dict])
async def inject_fault(
    payload: DebugFaultInjectRequest,
    client: AuthenticatedClient = Depends(get_current_client),
    settings: Settings = Depends(get_settings),
    self_injector: FaultInjector = Depends(get_fault_injector_self),
) -> Envelope[dict]:
    # One fixed scope for both verbs here (required_scope("POST"/"DELETE",
    # "debug") both resolve to "debug:write" — see authorization.py's
    # method-to-action map), not per-target scopes: this is a single
    # operator capability, not a resource with its own read/write split.
    check_scope(client.scopes, required_scope("POST", "debug"))

    if payload.target == "gateway":
        await self_injector.enable(payload.mode, payload.error_rate, payload.latency_ms, payload.duration_seconds)
        return Envelope(data={"status": "enabled", "target": "gateway", "component": "self"})

    await _forward_enable(payload, settings)
    return Envelope(data={"status": "enabled", "target": payload.target})


@router.delete("/fault-inject", response_model=Envelope[dict])
async def clear_fault(
    target: str,
    client: AuthenticatedClient = Depends(get_current_client),
    settings: Settings = Depends(get_settings),
    self_injector: FaultInjector = Depends(get_fault_injector_self),
) -> Envelope[dict]:
    check_scope(client.scopes, required_scope("DELETE", "debug"))

    if target == "gateway":
        await self_injector.disable()
        return Envelope(data={"status": "cleared", "target": "gateway", "component": "self"})

    await _forward_disable(target, settings)
    return Envelope(data={"status": "cleared", "target": target})


def _resolve_forward(target: str, settings: Settings) -> tuple[str, str]:
    entry = _FORWARD_TARGETS.get(target)
    if entry is None:
        raise NotFoundError(f"no fault-injection target named '{target}'")
    settings_attr, component = entry
    return getattr(settings, settings_attr).rstrip("/"), component


async def _forward_enable(payload: DebugFaultInjectRequest, settings: Settings) -> None:
    base_url, component = _resolve_forward(payload.target, settings)
    body = FaultInjectionRequest(
        mode=payload.mode,
        error_rate=payload.error_rate,
        latency_ms=payload.latency_ms,
        duration_seconds=payload.duration_seconds,
        component=component,
    )
    # Short timeout — this is a control-plane call configuring a fault,
    # not a request on the payment data path, so it has none of
    # ADR-0012's retry-budget-headroom concerns.
    async with httpx.AsyncClient(timeout=5.0) as http_client:
        try:
            response = await http_client.post(
                f"{base_url}/internal/fault-injection", json=body.model_dump()
            )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                f"could not reach '{payload.target}' to configure fault injection: {exc}"
            ) from exc
    if response.status_code >= 400:
        raise DependencyUnavailableError(
            f"'{payload.target}' rejected fault-injection request: {response.text}"
        )


async def _forward_disable(target: str, settings: Settings) -> None:
    base_url, component = _resolve_forward(target, settings)
    async with httpx.AsyncClient(timeout=5.0) as http_client:
        try:
            response = await http_client.delete(
                f"{base_url}/internal/fault-injection", params={"component": component}
            )
        except httpx.HTTPError as exc:
            raise DependencyUnavailableError(
                f"could not reach '{target}' to clear fault injection: {exc}"
            ) from exc
    if response.status_code >= 400:
        raise DependencyUnavailableError(f"'{target}' rejected fault-injection clear: {response.text}")
