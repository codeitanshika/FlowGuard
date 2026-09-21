from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="USER_")

    service_name: str = "user"
    port: int = 8003
    database_url: str
    # Shared infra var, deliberately not prefixed with USER_. Only used
    # for fault injection (Phase 6) — User Service has no other Redis
    # need.
    redis_url: str = Field(validation_alias="REDIS_URL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
