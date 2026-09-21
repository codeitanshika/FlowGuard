from typing import Literal

from pydantic import BaseModel, Field

from shared.fault_injection import MAX_DURATION_SECONDS, MAX_LATENCY_MS, FaultMode


class LoginRequest(BaseModel):
    client_id: str = Field(min_length=1)
    client_secret: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class AuthenticatedClient(BaseModel):
    client_id: str
    scopes: list[str]


class DebugFaultInjectRequest(BaseModel):
    """Body for POST /api/v1/debug/fault-inject. "gateway" is handled
    in-process (this service's own FaultInjector); every other target is
    forwarded over HTTP to that service's /internal/fault-injection —
    "payment-provider" specifically maps to Payment Service with
    component="provider" rather than being its own network target, since
    MockPaymentProvider has no HTTP surface of its own. See
    app/api/debug.py."""

    target: Literal["gateway", "payment", "fraud", "user", "notification", "payment-provider"]
    mode: FaultMode
    error_rate: float = Field(default=1.0, ge=0.0, le=1.0)
    latency_ms: int = Field(default=0, ge=0, le=MAX_LATENCY_MS)
    duration_seconds: int = Field(default=30, ge=1, le=MAX_DURATION_SECONDS)
