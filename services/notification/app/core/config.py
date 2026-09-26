from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="NOTIFICATION_")

    service_name: str = "notification"
    port: int = 8004
    database_url: str
    # Shared infra var, deliberately not prefixed with NOTIFICATION_.
    redis_url: str = Field(validation_alias="REDIS_URL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
