"""Provider-independent LLM contract.

The rest of the application only knows this module: it hands a ``Prompt`` to
an ``LLMProvider`` and gets back a validated ``AnalysisResult``. Which model
answered, over which API, is the provider's concern. Swapping OpenAI for a
local model means adding one class here, not touching business logic.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass

from pydantic import ValidationError

from app.schemas.analysis import AnalysisResult
from app.services.prompt_builder import Prompt


class LLMError(Exception):
    """Base class for anything that goes wrong while talking to a model."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached or refused the request (network, auth, rate limit)."""


class LLMInvalidResponseError(LLMError):
    """The provider answered, but the answer does not fit the result schema."""


@dataclass(frozen=True)
class LLMResponse:
    result: AnalysisResult
    model: str  # which model produced it, for logs and metrics
    input_tokens: int | None = None
    output_tokens: int | None = None


class LLMProvider(ABC):
    name: str  # short identifier used in logs and metrics, e.g. "openai", "fake"

    @abstractmethod
    async def analyze(self, prompt: Prompt) -> LLMResponse:
        """Send the prompt to the model and return a validated result.

        Raises ``LLMUnavailableError`` when the provider cannot answer and
        ``LLMInvalidResponseError`` when the answer fails schema validation.
        """


def parse_result(raw: str | bytes) -> AnalysisResult:
    """Validate raw model output against the schema; never let invalid data through."""
    try:
        return AnalysisResult.model_validate_json(raw)
    except ValidationError as exc:
        raise LLMInvalidResponseError(f"model output does not match the schema: {exc}") from exc
