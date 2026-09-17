from functools import lru_cache

from pydantic_settings import SettingsConfigDict

from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="USER_")

    service_name: str = "user"
    port: int = 8003
    database_url: str


@lru_cache
def get_settings() -> Settings:
    return Settings()
