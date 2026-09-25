from fastapi import APIRouter, Depends, Request

from app.clients.paypal_provider import PayPalProvider
from app.clients.paypal_webhooks import PayPalWebhookVerifier, WebhookVerificationError
from app.core.dependencies import get_breakers, get_fault_injectors, get_paypal_provider, get_paypal_webhook_verifier
from app.models.schemas import BreakerStatus
from shared.circuit_breaker import CircuitBreaker
from shared.errors import NotFoundError, ValidationAppError
from shared.fault_injection import FaultInjectionRequest, FaultInjector
from shared.logging import get_logger
from shared.schemas import Envelope

logger = get_logger(__name__)

# Never routed by the Gateway (see docs/architecture/03-service-boundaries.md)
# — reachable only on the internal network, by the Ops Controller
# (Phase 8) on the Healer Agent's behalf.
router = APIRouter(prefix="/internal", tags=["internal"])


@router.get("/circuit-breakers", response_model=Envelope[dict[str, str]])
async def list_breakers(breakers: dict[str, CircuitBreaker] = Depends(get_breakers)) -> Envelope[dict[str, str]]:
    # Read-only: lets the Healer (via the Ops Controller) see current
    # breaker state before deciding whether an action is even needed.
    return Envelope(data={name: (await b.state()).value for name, b in breakers.items()})


@router.post("/circuit-breakers/{dependency}/open", response_model=Envelope[BreakerStatus])
async def open_breaker(
    dependency: str, breakers: dict[str, CircuitBreaker] = Depends(get_breakers)
) -> Envelope[BreakerStatus]:
    breaker = _resolve(dependency, breakers)
    await breaker.force_open()
    return Envelope(data=BreakerStatus(dependency=dependency, state=(await breaker.state()).value))


@router.post("/circuit-breakers/{dependency}/reset", response_model=Envelope[BreakerStatus])
async def reset_breaker(
    dependency: str, breakers: dict[str, CircuitBreaker] = Depends(get_breakers)
) -> Envelope[BreakerStatus]:
    breaker = _resolve(dependency, breakers)
    await breaker.reset()
    return Envelope(data=BreakerStatus(dependency=dependency, state=(await breaker.state()).value))


def _resolve(dependency: str, breakers: dict[str, CircuitBreaker]) -> CircuitBreaker:
    if dependency not in breakers:
        raise NotFoundError(f"no circuit breaker named '{dependency}'")
    return breakers[dependency]


@router.post("/fault-injection", response_model=Envelope[dict])
async def configure_fault(
    payload: FaultInjectionRequest, injectors: dict[str, FaultInjector] = Depends(get_fault_injectors)
) -> Envelope[dict]:
    # component defaults to "self" (this service's own inbound HTTP
    # surface); pass component="provider" to target MockPaymentProvider's
    # capture() call specifically — see provider_client.py.
    injector = _resolve_injector(payload.component, injectors)
    await injector.enable(payload.mode, payload.error_rate, payload.latency_ms, payload.duration_seconds)
    return Envelope(data={"status": "enabled", "component": payload.component})


@router.delete("/fault-injection", response_model=Envelope[dict])
async def clear_fault(
    component: str = "self", injectors: dict[str, FaultInjector] = Depends(get_fault_injectors)
) -> Envelope[dict]:
    injector = _resolve_injector(component, injectors)
    await injector.disable()
    return Envelope(data={"status": "cleared", "component": component})


def _resolve_injector(component: str, injectors: dict[str, FaultInjector]) -> FaultInjector:
    if component not in injectors:
        raise NotFoundError(f"no fault injector for component '{component}' (expected 'self' or 'provider')")
    return injectors[component]


@router.get("/providers/paypal/captures/{capture_id}", response_model=Envelope[dict])
async def paypal_capture_status(
    capture_id: str, provider: PayPalProvider = Depends(get_paypal_provider)
) -> Envelope[dict]:
    """Status lookup (FR13) — diagnostic use, not on the synchronous
    capture path (Transaction.status is already the source of truth for
    that). 404s if PayPal isn't the active provider."""
    return Envelope(data=await provider.get_capture_status(capture_id))


@router.post("/providers/paypal/webhook", response_model=Envelope[dict])
async def paypal_webhook(
    request: Request, verifier: PayPalWebhookVerifier = Depends(get_paypal_webhook_verifier)
) -> Envelope[dict]:
    """Receives PayPal webhook deliveries (FR13's "webhook handling").
    Verified via PayPal's own verify-webhook-signature API rather than
    local certificate/crypto verification — see paypal_webhooks.py.
    Never routed by the Gateway; PayPal calls this directly, so it can't
    carry the Gateway's own auth — verification of the PayPal signature
    headers is what authenticates the caller here instead. 404s if
    PAYPAL_WEBHOOK_ID isn't configured."""
    event = await request.json()
    try:
        await verifier.verify(dict(request.headers), event)
    except WebhookVerificationError as exc:
        logger.warning("payment.paypal_webhook_rejected", error=str(exc))
        raise ValidationAppError(f"webhook verification failed: {exc}") from exc

    event_type = event.get("event_type", "unknown")
    resource_id = event.get("resource", {}).get("id")
    logger.info("payment.paypal_webhook_received", event_type=event_type, resource_id=resource_id)
    return Envelope(data={"status": "received", "event_type": event_type})
