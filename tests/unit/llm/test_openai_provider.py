"""OpenAIProvider tests with a stub client: no network, no API key."""

from types import SimpleNamespace
from typing import Any

import httpx2 as httpx
import openai
import pytest

from app.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.llm.fake import DEFAULT_FAKE_RESULT
from app.llm.openai_provider import OpenAIProvider, strict_schema
from app.schemas.analysis import AnalysisResult
from app.services.prompt_builder import PromptBuilder

PROMPT = PromptBuilder().build("[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.")


def _completion(content: str | None, *, refusal: str | None = None) -> Any:
    """The minimal shape of a ChatCompletion the provider reads."""
    return SimpleNamespace(
        model="gpt-test-2026",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, refusal=refusal))],
        usage=SimpleNamespace(prompt_tokens=321, completion_tokens=87),
    )


class StubClient:
    """Stands in for AsyncOpenAI; records the request and returns/raises what it was given."""

    def __init__(self, *, returns: Any = None, raises: Exception | None = None) -> None:
        self.requests: list[dict[str, Any]] = []
        self._returns = returns
        self._raises = raises
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        if self._raises is not None:
            raise self._raises
        return self._returns


def _provider(client: StubClient) -> OpenAIProvider:
    return OpenAIProvider(api_key="unused", model="gpt-test-2026", client=client)  # type: ignore[arg-type]


def _api_error(cls: type[openai.APIStatusError], status: int) -> openai.APIStatusError:
    response = httpx.Response(status, request=httpx.Request("POST", "https://api.openai.com"))
    return cls("boom", response=response, body=None)


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


async def test_valid_completion_becomes_a_validated_result() -> None:
    client = StubClient(returns=_completion(DEFAULT_FAKE_RESULT.model_dump_json()))

    response = await _provider(client).analyze(PROMPT)

    assert response.result == DEFAULT_FAKE_RESULT
    assert response.model == "gpt-test-2026"
    assert (response.input_tokens, response.output_tokens) == (321, 87)


async def test_request_carries_system_and_user_messages_and_strict_schema() -> None:
    client = StubClient(returns=_completion(DEFAULT_FAKE_RESULT.model_dump_json()))

    await _provider(client).analyze(PROMPT)

    request = client.requests[0]
    assert request["model"] == "gpt-test-2026"
    assert request["messages"] == [
        {"role": "system", "content": PROMPT.system},
        {"role": "user", "content": PROMPT.user},
    ]
    fmt = request["response_format"]
    assert fmt["type"] == "json_schema"
    assert fmt["json_schema"]["strict"] is True
    assert fmt["json_schema"]["schema"]["additionalProperties"] is False


# ---------------------------------------------------------------------------
# error mapping: transient -> LLMUnavailableError, bad answer -> LLMInvalidResponseError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exc",
    [
        openai.APIConnectionError(request=httpx.Request("POST", "https://api.openai.com")),
        _api_error(openai.RateLimitError, 429),
        _api_error(openai.InternalServerError, 500),
        _api_error(openai.AuthenticationError, 401),
    ],
)
async def test_transient_and_auth_failures_are_unavailable(exc: Exception) -> None:
    client = StubClient(raises=exc)

    with pytest.raises(LLMUnavailableError):
        await _provider(client).analyze(PROMPT)


async def test_client_side_4xx_is_not_retryable() -> None:
    client = StubClient(raises=_api_error(openai.BadRequestError, 400))

    with pytest.raises(LLMInvalidResponseError):
        await _provider(client).analyze(PROMPT)


@pytest.mark.parametrize(
    "completion",
    [
        _completion('{"status": "failed"}'),  # missing fields
        _completion("I cannot help with that."),  # not JSON
        _completion(None),  # empty
        _completion(None, refusal="I refuse."),  # explicit refusal
    ],
)
async def test_bad_answers_are_invalid_responses(completion: Any) -> None:
    client = StubClient(returns=completion)

    with pytest.raises(LLMInvalidResponseError):
        await _provider(client).analyze(PROMPT)


# ---------------------------------------------------------------------------
# strict schema conversion
# ---------------------------------------------------------------------------


def test_strict_schema_requires_every_field_and_drops_unsupported_keywords() -> None:
    schema = strict_schema(AnalysisResult)

    assert set(schema["required"]) == set(schema["properties"])
    assert schema["additionalProperties"] is False

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                assert key not in {
                    "minLength",
                    "maxLength",
                    "minItems",
                    "maxItems",
                    "minimum",
                    "maximum",
                    "default",
                }
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)


def test_strict_schema_never_puts_keywords_next_to_a_ref() -> None:
    """The first real OpenAI call failed with: $ref cannot have keywords {'description'}."""
    schema = strict_schema(AnalysisResult)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if "$ref" in node:
                assert set(node) == {"$ref"}, node
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema)
    assert schema["properties"]["category"] == {"$ref": "#/$defs/FailureCategory"}


def test_strict_schema_keeps_the_category_enum() -> None:
    schema = strict_schema(AnalysisResult)

    assert "enum" in schema["$defs"]["FailureCategory"]
