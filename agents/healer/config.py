from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from agents.monitor.thresholds import Thresholds
from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="HEALER_")

    service_name: str = "healer"
    port: int = 8007
    database_url: str
    redis_url: str = Field(validation_alias="REDIS_URL")
    jaeger_query_url: str = "http://localhost:16686"

    ops_controller_url: str = "http://localhost:8006"
    # Shared with the Ops Controller, which holds the same value as its
    # OPS_HEALER_TOKEN. No default: the Healer cannot act without it.
    ops_token: str = Field(validation_alias="OPS_HEALER_TOKEN", min_length=16)

    # Empty/unset disables the LLM entirely; the deterministic rules decide.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    llm_model: str = "claude-opus-5"
    llm_timeout_seconds: float = 30.0

    # Verification: after acting, re-measure the affected service over a
    # short window every interval until it recovers or the timeout passes.
    verify_interval_seconds: int = Field(default=15, ge=1)
    verify_window_seconds: int = Field(default=30, ge=5)
    verify_timeout_seconds: int = Field(default=300, ge=10)
    max_concurrent_incidents: int = Field(default=10, ge=1)
    # Before force-opening a breaker, confirm the failure is still going on
    # in the most recent requests (see verifier.check_still_failing).
    precheck_window_seconds: int = Field(default=30, ge=5)
    precheck_recent_samples: int = Field(default=5, ge=1)

    # Must match the Monitor's thresholds so "recovered" means exactly
    # "the Monitor would not alert" (keep MONITOR_/HEALER_ overrides in sync).
    default_thresholds: Thresholds = Thresholds()
    service_thresholds: dict[str, dict[str, float]] = {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
