from functools import lru_cache

from pydantic import Field
from pydantic_settings import SettingsConfigDict

from agents.monitor.thresholds import Thresholds
from shared.config import BaseServiceSettings


class Settings(BaseServiceSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_prefix="MONITOR_")

    service_name: str = "monitor"
    port: int = 8005
    database_url: str
    # Shared infra var, deliberately not prefixed with MONITOR_.
    redis_url: str = Field(validation_alias="REDIS_URL")
    jaeger_query_url: str = "http://localhost:16686"

    poll_interval_seconds: int = Field(default=15, ge=1)
    # Longer than the poll interval so consecutive evaluations overlap and
    # smooth over spans that are exported a few seconds after they end.
    window_seconds: int = Field(default=60, ge=5)
    # Jaeger's query API caps traces per call; see ADR-0014.
    max_traces: int = Field(default=1000, ge=1)
    alert_cooldown_seconds: int = Field(default=120, ge=1)

    # Names must match each service's OTel service.name.
    monitored_services: list[str] = ["gateway", "payment", "user", "fraud", "notification"]
    default_thresholds: Thresholds = Thresholds()
    # e.g. MONITOR_SERVICE_THRESHOLDS='{"payment":{"p95_warning_ms":500}}'
    service_thresholds: dict[str, dict[str, float]] = {}


@lru_cache
def get_settings() -> Settings:
    return Settings()
