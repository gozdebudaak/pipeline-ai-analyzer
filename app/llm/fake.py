"""A stand-in provider for tests and local development.

It never touches the network. It returns whatever it was configured with,
records every prompt it receives, and can be told to misbehave (malformed
output, provider outage) so the callers' error handling can be tested.
"""

from app.llm.base import LLMError, LLMProvider, LLMResponse, parse_result
from app.schemas.analysis import AnalysisResult
from app.services.failure_classifier import FailureCategory
from app.services.prompt_builder import Prompt

DEFAULT_FAKE_RESULT = AnalysisResult(
    status="failed",
    category=FailureCategory.AUTHENTICATION,
    severity="high",
    summary="The build failed while fetching a dependency from the artifact repository.",
    root_cause="The repository returned 401 Unauthorized for the configured credentials.",
    confidence=0.9,
    evidence=["401 Unauthorized"],
    suggested_actions=["Verify the repository credentials configured in the pipeline."],
)


class FakeLLMProvider(LLMProvider):
    name = "fake"

    def __init__(
        self,
        result: AnalysisResult = DEFAULT_FAKE_RESULT,
        *,
        raw_output: str | None = None,
        error: LLMError | None = None,
        model: str = "fake-model",
    ) -> None:
        self._result = result
        self._raw_output = raw_output  # if set, goes through schema validation like real output
        self._error = error
        self._model = model
        self.calls: list[Prompt] = []  # every prompt received, for assertions

    async def analyze(self, prompt: Prompt) -> LLMResponse:
        self.calls.append(prompt)
        if self._error is not None:
            raise self._error
        result = parse_result(self._raw_output) if self._raw_output is not None else self._result
        return LLMResponse(result=result, model=self._model, input_tokens=100, output_tokens=50)
