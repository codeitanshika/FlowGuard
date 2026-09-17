from functools import lru_cache

from pydantic import Field
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


@lru_cache
def get_settings() -> Settings:
    return Settings()
