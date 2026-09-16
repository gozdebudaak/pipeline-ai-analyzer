"""The service reports what it does through Prometheus metrics; these read them back."""

import pytest
from prometheus_client import REGISTRY

from app.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.llm.fake import DEFAULT_FAKE_RESULT, FakeLLMProvider
from app.services.analysis_service import AnalysisService
from app.services.failure_classifier import FailureClassifier
from app.services.log_processor import LogProcessor
from app.services.prompt_builder import PromptBuilder
from app.services.secret_redactor import SecretRedactor

LOG = "[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.\nPASSWORD=hunter2\n"


def _service(provider: FakeLLMProvider) -> AnalysisService:
    return AnalysisService(
        redactor=SecretRedactor(),
        processor=LogProcessor(),
        classifier=FailureClassifier(),
        prompt_builder=PromptBuilder(),
        provider=provider,
    )


def _value(name: str, **labels: str) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


async def test_success_is_counted_with_category_and_agreement() -> None:
    before = _value("analysis_total", outcome="success")
    agree = _value("analysis_category_total", category="authentication", agreement="agree")

    await _service(FakeLLMProvider()).analyze(LOG)  # fake says authentication, rules say 401

    assert _value("analysis_total", outcome="success") == before + 1
    assert (
        _value("analysis_category_total", category="authentication", agreement="agree") == agree + 1
    )


async def test_disagreement_is_labelled() -> None:
    before = _value("analysis_category_total", category="authentication", agreement="disagree")

    await _service(FakeLLMProvider()).analyze("[ERROR] COMPILATION ERROR :\n")

    assert (
        _value("analysis_category_total", category="authentication", agreement="disagree")
        == before + 1
    )


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (LLMUnavailableError("429"), "llm_unavailable"),
        (LLMInvalidResponseError("bad json"), "llm_invalid_response"),
    ],
)
async def test_provider_failures_are_counted_by_kind(error: Exception, outcome: str) -> None:
    before = _value("analysis_total", outcome=outcome)
    success_before = _value("analysis_total", outcome="success")

    with pytest.raises(type(error)):
        await _service(FakeLLMProvider(error=error)).analyze(LOG)  # type: ignore[arg-type]

    assert _value("analysis_total", outcome=outcome) == before + 1
    assert _value("analysis_total", outcome="success") == success_before


async def test_tokens_and_duration_are_recorded_per_provider() -> None:
    tokens_in = _value("llm_tokens_total", provider="fake", direction="input")
    tokens_out = _value("llm_tokens_total", provider="fake", direction="output")
    calls = _value("llm_request_duration_seconds_count", provider="fake")

    await _service(FakeLLMProvider()).analyze(LOG)

    assert _value("llm_tokens_total", provider="fake", direction="input") == tokens_in + 100
    assert _value("llm_tokens_total", provider="fake", direction="output") == tokens_out + 50
    assert _value("llm_request_duration_seconds_count", provider="fake") == calls + 1


async def test_duration_is_recorded_even_when_the_call_fails() -> None:
    calls = _value("llm_request_duration_seconds_count", provider="fake")

    with pytest.raises(LLMUnavailableError):
        await _service(FakeLLMProvider(error=LLMUnavailableError("down"))).analyze(LOG)

    assert _value("llm_request_duration_seconds_count", provider="fake") == calls + 1


async def test_redacted_secrets_are_counted_by_pattern() -> None:
    before = _value("secrets_redacted_total", pattern="key_value")

    await _service(FakeLLMProvider()).analyze(LOG)  # PASSWORD=hunter2 -> key_value

    assert _value("secrets_redacted_total", pattern="key_value") == before + 1


async def test_excerpt_size_is_observed() -> None:
    before = _value("analysis_excerpt_tokens_count")

    await _service(FakeLLMProvider()).analyze(LOG)

    assert _value("analysis_excerpt_tokens_count") == before + 1
    assert DEFAULT_FAKE_RESULT.category.value == "authentication"  # documents the fake's answer
