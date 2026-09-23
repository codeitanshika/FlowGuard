from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="OPS_")

    service_name: str = "ops-controller"
    port: int = 8006
    database_url: str
    redis_url: str = Field(validation_alias="REDIS_URL")

    # No default: an Ops Controller without a caller secret must fail at
    # startup, not run open. Shared with the Healer as OPS_HEALER_TOKEN.
    healer_token: str = Field(min_length=16)

    # The only base URLs an action can ever reach. Callers name a service;
    # they never supply a URL.
    payment_service_url: str
    request_timeout_seconds: float = 5.0
    # Minimum gap between two executions of the same action on the same
    # target — bounds how fast a misbehaving caller can flap a breaker.
    action_cooldown_seconds: int = Field(default=60, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
