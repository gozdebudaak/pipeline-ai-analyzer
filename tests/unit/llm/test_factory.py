import pytest

from app.core.config import Settings
from app.llm.factory import LLMConfigurationError, build_llm_provider
from app.llm.fake import FakeLLMProvider
from app.llm.openai_provider import OpenAIProvider


def test_test_environment_always_gets_the_fake_provider() -> None:
    settings = Settings(_env_file=None, app_env="test", openai_api_key="sk-real-looking-key")

    assert isinstance(build_llm_provider(settings), FakeLLMProvider)


def test_missing_api_key_outside_tests_is_a_configuration_error() -> None:
    settings = Settings(_env_file=None, app_env="development", openai_api_key=None)

    with pytest.raises(LLMConfigurationError):
        build_llm_provider(settings)


def test_api_key_outside_tests_builds_the_openai_provider() -> None:
    settings = Settings(
        _env_file=None, app_env="development", openai_api_key="sk-x", openai_model="gpt-test"
    )

    provider = build_llm_provider(settings)

    assert isinstance(provider, OpenAIProvider)
    assert provider.name == "openai"


def test_api_key_never_appears_in_settings_repr() -> None:
    settings = Settings(_env_file=None, openai_api_key="sk-super-secret")

    assert "sk-super-secret" not in repr(settings)
    assert "sk-super-secret" not in str(settings)
