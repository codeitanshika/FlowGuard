from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from app.core.clients import ClientCredential
from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="GATEWAY_")

    service_name: str = "gateway"
    port: int = 8000
    payment_service_url: str
    user_service_url: str
    notification_service_url: str
    # Not part of the route table (build_route_table) — Fraud Service is
    # never reachable through /api/v1/*, only internally by Payment. This
    # exists solely so the Phase 6 debug endpoint can forward
    # fault-injection control calls to Fraud's own /internal/fault-injection.
    fraud_service_url: str

    # Shared infra var, deliberately not prefixed with GATEWAY_.
    redis_url: str = Field(validation_alias="REDIS_URL")

    # Phase 2 — security. jwt_secret and clients have no default: a
    # misconfigured deployment must fail at startup, not silently run
    # with an insecure/empty auth configuration.
    jwt_secret: str
    jwt_expires_in: int = 3600
    clients: dict[str, ClientCredential]
    rate_limit_per_minute: int = 100
    login_rate_limit_per_minute: int = 10

    # Off by default — local dev and tests see the raw TCP peer, exactly as
    # before. A production deployment behind Caddy (ADR-0020) sets this,
    # or every caller's request.client.host is Caddy's own container IP:
    # the per-IP login rate limiter (app/api/auth.py) would key every real
    # user's login attempt as "the same client", sharing one 10/min budget
    # across the whole internet. Safe to trust unconditionally here
    # (app/main.py) specifically because nothing in the production topology
    # can reach this service directly — Caddy is the only path in.
    trust_proxy_headers: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()
