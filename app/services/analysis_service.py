"""Orchestrates one log analysis end to end.

    raw log -> redact -> preprocess -> classify (rules) -> prompt -> LLM
            -> validate -> reconcile rule-based vs model category -> outcome

The service owns the order and the reconciliation; it knows nothing about
HTTP, Jenkins or OpenAI. Every collaborator is injected so the whole flow
is testable with the fake provider.
"""

import logging
from dataclasses import dataclass

from app.llm.base import LLMProvider
from app.schemas.analysis import AnalysisResult
from app.services.failure_classifier import Classification, FailureCategory, FailureClassifier
from app.services.log_processor import LogProcessor
from app.services.prompt_builder import PromptBuilder
from app.services.secret_redactor import SecretRedactor

logger = logging.getLogger(__name__)

# When the model and the rule-based classifier disagree, two independent
# opinions conflict: scale the model's confidence down. Tuned in MVP 2
# against the evaluation dataset.
DISAGREEMENT_CONFIDENCE_FACTOR = 0.7


@dataclass(frozen=True)
class LogStats:
    total_lines: int
    normalized_lines: int
    excerpt_lines: int
    truncated: bool
    secrets_redacted: int


@dataclass(frozen=True)
class AnalysisOutcome:
    result: AnalysisResult  # the model's analysis, confidence possibly reconciled
    rule_based_category: FailureCategory
    matched_rules: list[str]
    category_agreement: bool | None  # None when the rules had no opinion
    log_stats: LogStats
    llm_provider: str
    llm_model: str
    input_tokens: int | None
    output_tokens: int | None
    prompt_version: str


def reconcile(
    result: AnalysisResult, classification: Classification
) -> tuple[AnalysisResult, bool | None]:
    """Compare the model's category with the rule-based one and adjust confidence.

    Returns the (possibly adjusted) result and whether the two agreed;
    ``None`` when the rules matched nothing and there is nothing to compare.
    """
    if not classification.is_known:
        return result, None
    if result.category is classification.category:
        return result, True
    adjusted = result.model_copy(
        update={"confidence": round(result.confidence * DISAGREEMENT_CONFIDENCE_FACTOR, 3)}
    )
    return adjusted, False


class AnalysisService:
    def __init__(
        self,
        *,
        redactor: SecretRedactor,
        processor: LogProcessor,
        classifier: FailureClassifier,
        prompt_builder: PromptBuilder,
        provider: LLMProvider,
    ) -> None:
        self._redactor = redactor
        self._processor = processor
        self._classifier = classifier
        self._prompt_builder = prompt_builder
        self._provider = provider

    async def analyze(self, raw_log: str) -> AnalysisOutcome:
        if not raw_log.strip():
            raise ValueError("log is empty")

        redacted = self._redactor.redact(raw_log)
        processed = self._processor.process(redacted.text)
        classification = self._classifier.classify(processed.error_lines)
        prompt = self._prompt_builder.build(processed.excerpt)

        response = await self._provider.analyze(prompt)
        result, agreement = reconcile(response.result, classification)

        stats = LogStats(
            total_lines=processed.total_lines,
            normalized_lines=processed.normalized_lines,
            excerpt_lines=processed.excerpt_lines,
            truncated=processed.truncated,
            secrets_redacted=redacted.total,
        )
        logger.info(
            "analysis completed",
            extra={
                "category": result.category.value,
                "rule_based_category": classification.category.value,
                "category_agreement": agreement,
                "confidence": result.confidence,
                "severity": result.severity,
                "secrets_redacted": stats.secrets_redacted,
                "lines": f"{stats.total_lines}->{stats.normalized_lines}->{stats.excerpt_lines}",
                "llm_provider": self._provider.name,
                "llm_model": response.model,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
            },
        )
        return AnalysisOutcome(
            result=result,
            rule_based_category=classification.category,
            matched_rules=classification.matched_rules,
            category_agreement=agreement,
            log_stats=stats,
            llm_provider=self._provider.name,
            llm_model=response.model,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            prompt_version=prompt.version,
        )
