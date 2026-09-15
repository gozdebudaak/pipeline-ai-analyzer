"""Application configuration.

Settings are read from environment variables (and an optional ``.env`` file)
and validated once at startup. Anything that differs between environments
(development, CI, production) belongs here; nothing else should read
``os.environ`` directly.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AppEnv = Literal["development", "test", "production"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "pipeline-ai-analyzer"
    app_version: str = "0.1.0"
    app_env: AppEnv = "development"
    log_level: LogLevel = "INFO"

    # LLM provider. SecretStr prints as ********** in logs and reprs.
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-5.4-mini"
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 2  # only for transient failures (network, 429, 5xx)

    @field_validator("openai_api_key", mode="before")
    @classmethod
    def _empty_key_is_missing(cls, value: object) -> object:
        # docker-compose renders an unset variable as "" ; treat that as "not configured".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
