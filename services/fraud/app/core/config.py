from functools import lru_cache

from pydantic_settings import SettingsConfigDict

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="FRAUD_")

    service_name: str = "fraud"
    port: int = 8002
    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
