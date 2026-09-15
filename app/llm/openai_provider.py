"""OpenAI implementation of :class:`LLMProvider`.

Uses chat completions with a JSON-schema response format so the model is
constrained to the result shape server-side. The output still passes
through ``parse_result``: the schema sent to OpenAI is a shape hint, our
Pydantic validation is the law.
"""

import logging
from typing import Any

import openai
from openai import AsyncOpenAI
from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)
from openai.types.shared_params import ResponseFormatJSONSchema

from app.llm.base import (
    LLMInvalidResponseError,
    LLMProvider,
    LLMResponse,
    LLMUnavailableError,
    parse_result,
)
from app.schemas.analysis import AnalysisResult
from app.services.prompt_builder import Prompt

logger = logging.getLogger(__name__)

# JSON Schema keywords OpenAI's strict mode does not accept. Our Pydantic
# model still enforces them when the answer comes back.
_UNSUPPORTED_KEYWORDS = frozenset(
    {
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "pattern",
        "format",
        "default",
    }
)


def strict_schema(model: type[AnalysisResult]) -> dict[str, Any]:
    """Pydantic schema -> OpenAI strict schema: all required, no extras, no unsupported keys."""

    def clean(node: Any) -> Any:
        if isinstance(node, dict):
            cleaned = {k: clean(v) for k, v in node.items() if k not in _UNSUPPORTED_KEYWORDS}
            if cleaned.get("type") == "object" and "properties" in cleaned:
                cleaned["required"] = list(cleaned["properties"])
                cleaned["additionalProperties"] = False
            return cleaned
        if isinstance(node, list):
            return [clean(item) for item in node]
        return node

    return clean(model.model_json_schema())  # type: ignore[no-any-return]


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        client: AsyncOpenAI | None = None,  # injectable for tests
    ) -> None:
        self._model = model
        self._client = client or AsyncOpenAI(
            api_key=api_key, timeout=timeout_seconds, max_retries=max_retries
        )
        self._response_format: ResponseFormatJSONSchema = {
            "type": "json_schema",
            "json_schema": {
                "name": "analysis_result",
                "strict": True,
                "schema": strict_schema(AnalysisResult),
            },
        }

    async def analyze(self, prompt: Prompt) -> LLMResponse:
        messages: list[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(role="system", content=prompt.system),
            ChatCompletionUserMessageParam(role="user", content=prompt.user),
        ]
        try:
            # No temperature: GPT-5 family models reject it; structured output keeps the shape.
            completion = await self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                response_format=self._response_format,
            )
        except (
            openai.APIConnectionError,
            openai.RateLimitError,
            openai.InternalServerError,
        ) as exc:
            raise LLMUnavailableError(f"openai unavailable: {exc}") from exc
        except openai.AuthenticationError as exc:
            raise LLMUnavailableError("openai rejected the API key") from exc
        except openai.APIStatusError as exc:
            # Other 4xx: our request was wrong (bad model name, schema...) -> not retryable.
            raise LLMInvalidResponseError(f"openai returned {exc.status_code}: {exc}") from exc

        message = completion.choices[0].message
        if message.refusal:
            raise LLMInvalidResponseError(f"model refused to answer: {message.refusal}")
        if not message.content:
            raise LLMInvalidResponseError("model returned an empty response")

        try:
            result = parse_result(message.content)
        except LLMInvalidResponseError:
            # Keep the raw output in the logs: this is what fixes the prompt.
            logger.warning("invalid model output", extra={"raw_output": message.content[:2000]})
            raise

        usage = completion.usage
        return LLMResponse(
            result=result,
            model=completion.model,
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )
