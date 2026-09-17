from functools import lru_cache

from pydantic_settings import SettingsConfigDict

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="GATEWAY_")

    service_name: str = "gateway"
    port: int = 8000
    payment_service_url: str
    user_service_url: str
    notification_service_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
