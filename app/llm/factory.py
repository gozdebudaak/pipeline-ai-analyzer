"""Decides which LLM provider the application uses. The only place that knows all of them."""

from app.core.config import Settings
from app.llm.base import LLMProvider
from app.llm.fake import FakeLLMProvider
from app.llm.openai_provider import OpenAIProvider


class LLMConfigurationError(RuntimeError):
    pass


def build_llm_provider(settings: Settings) -> LLMProvider:
    # Tests must never reach a real model, whatever else is configured.
    if settings.app_env == "test":
        return FakeLLMProvider()

    if settings.openai_api_key is None:
        raise LLMConfigurationError(
            "OPENAI_API_KEY is not set; the analysis service cannot start without an LLM provider"
        )
    return OpenAIProvider(
        api_key=settings.openai_api_key.get_secret_value(),
        model=settings.openai_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
    )
