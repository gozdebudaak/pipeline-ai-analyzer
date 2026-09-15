import json

import pytest

from app.llm.base import (
    LLMInvalidResponseError,
    LLMProvider,
    LLMUnavailableError,
    parse_result,
)
from app.llm.fake import DEFAULT_FAKE_RESULT, FakeLLMProvider
from app.services.failure_classifier import FailureCategory
from app.services.prompt_builder import PromptBuilder

PROMPT = PromptBuilder().build("[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.")


async def test_fake_provider_returns_configured_result() -> None:
    provider = FakeLLMProvider()

    response = await provider.analyze(PROMPT)

    assert response.result == DEFAULT_FAKE_RESULT
    assert response.model == "fake-model"
    assert response.input_tokens == 100


async def test_fake_provider_records_every_prompt() -> None:
    provider = FakeLLMProvider()

    await provider.analyze(PROMPT)
    await provider.analyze(PROMPT)

    assert provider.calls == [PROMPT, PROMPT]


async def test_fake_provider_can_simulate_malformed_output() -> None:
    provider = FakeLLMProvider(raw_output='{"category": "nonsense"}')

    with pytest.raises(LLMInvalidResponseError):
        await provider.analyze(PROMPT)


async def test_fake_provider_can_simulate_an_outage() -> None:
    provider = FakeLLMProvider(error=LLMUnavailableError("rate limited"))

    with pytest.raises(LLMUnavailableError, match="rate limited"):
        await provider.analyze(PROMPT)
    assert provider.calls == [PROMPT]  # the call was still recorded


async def test_fake_provider_valid_raw_output_is_parsed() -> None:
    raw = DEFAULT_FAKE_RESULT.model_dump_json()
    provider = FakeLLMProvider(raw_output=raw)

    response = await provider.analyze(PROMPT)

    assert response.result.category is FailureCategory.AUTHENTICATION


def test_fake_provider_is_an_llm_provider() -> None:
    assert isinstance(FakeLLMProvider(), LLMProvider)
    assert FakeLLMProvider.name == "fake"


# ---------------------------------------------------------------------------
# parse_result: the gate every provider's output must pass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "not json at all",
        'Sure! Here is the analysis: {"status": "failed"}',  # chatty prefix
        json.dumps({"status": "failed"}),  # missing fields
        json.dumps({**DEFAULT_FAKE_RESULT.model_dump(), "confidence": 5}),
        json.dumps({**DEFAULT_FAKE_RESULT.model_dump(), "extra": "field"}),
    ],
)
def test_parse_result_rejects_invalid_output(raw: str) -> None:
    with pytest.raises(LLMInvalidResponseError):
        parse_result(raw)


def test_parse_result_accepts_valid_output() -> None:
    assert parse_result(DEFAULT_FAKE_RESULT.model_dump_json()) == DEFAULT_FAKE_RESULT
