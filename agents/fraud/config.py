from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from agents.fraud.thresholds import VelocityThresholds
from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    # FRAUD_AGENT_, deliberately distinct from Fraud *Service*'s FRAUD_
    # prefix (services/fraud/app/core/config.py) — different components,
    # would otherwise collide on FRAUD_DATABASE_URL etc.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="FRAUD_AGENT_")

    service_name: str = "fraud-agent"
    port: int = 8008
    database_url: str
    # Shared infra var, deliberately not prefixed with FRAUD_AGENT_.
    redis_url: str = Field(validation_alias="REDIS_URL")
    user_service_url: str

    # Empty/unset disables the LLM entirely; the rules-based narrative is used.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    llm_model: str = "claude-opus-5"
    llm_timeout_seconds: float = 30.0

    thresholds: VelocityThresholds = VelocityThresholds()
    # Once a user is frozen for a velocity/geo reason, further high-risk
    # events for them within this window don't call freeze again (see
    # ADR-0016) — User Service's freeze is otherwise idempotent on status
    # but still writes a freeze_events audit row on every call.
    freeze_cooldown_seconds: int = Field(default=300, ge=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
