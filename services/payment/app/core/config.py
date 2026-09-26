from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import SettingsConfigDict

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="PAYMENT_")

    service_name: str = "payment"
    port: int = 8001
    database_url: str
    fraud_service_url: str
    user_service_url: str
    # Shared infra var, deliberately not prefixed with PAYMENT_.
    redis_url: str = Field(validation_alias="REDIS_URL")

    # Phase 10 — which PaymentProvider implementation is active. Server-side
    # deployment config, not client-supplied (see ADR-0017 and ADR-0010's
    # similar reasoning for GATEWAY_CLIENTS): a merchant backend chooses its
    # payment rail, individual API callers don't pick it per request.
    provider_backend: Literal["mock", "paypal"] = "mock"

    # PayPal credentials/config — unprefixed like ANTHROPIC_API_KEY/REDIS_URL:
    # vendor-level, not PAYMENT_-service-specific. No default for the
    # credentials: only required (and validated below) when
    # provider_backend="paypal", same "fail fast, don't run silently
    # misconfigured" pattern as GATEWAY_JWT_SECRET.
    paypal_client_id: str | None = Field(default=None, validation_alias="PAYPAL_CLIENT_ID")
    paypal_client_secret: str | None = Field(default=None, validation_alias="PAYPAL_CLIENT_SECRET")
    paypal_webhook_id: str | None = Field(default=None, validation_alias="PAYPAL_WEBHOOK_ID")
    paypal_api_base_url: str = Field(default="https://api-m.sandbox.paypal.com", validation_alias="PAYPAL_API_BASE_URL")

    @model_validator(mode="after")
    def _paypal_credentials_required_when_active(self) -> "Settings":
        if self.provider_backend == "paypal" and not (self.paypal_client_id and self.paypal_client_secret):
            raise ValueError("PAYMENT_PROVIDER_BACKEND=paypal requires PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
