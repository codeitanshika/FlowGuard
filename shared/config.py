from pydantic_settings import BaseSettings, SettingsConfigDict


class BaseServiceSettings(BaseSettings):
    """Common settings every service/agent extends. Subclasses must repeat
    model_config (rather than relying on inheritance-merge) so each service
    can set its own env_prefix explicitly."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    environment: str = "development"
    log_level: str = "INFO"
