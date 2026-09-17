from pathlib import Path

import pytest

from app.llm.base import LLMError, LLMInvalidResponseError, LLMUnavailableError
from app.llm.fake import DEFAULT_FAKE_RESULT, FakeLLMProvider
from app.services.analysis_service import AnalysisService, reconcile
from app.services.failure_classifier import Classification, FailureCategory, FailureClassifier
from app.services.log_processor import LogProcessor
from app.services.prompt_builder import PROMPT_VERSION, PromptBuilder
from app.services.secret_redactor import SecretRedactor

SAMPLE = (
    Path(__file__).resolve().parents[3] / "sample_logs" / "maven" / "dependency_resolution_401.log"
)
FAKE_ARTIFACTORY_KEY = "AKCp8k3Jd9sLx2mQw7vB4nH6tY1uZ0pR"  # planted in the sample log


def _service(provider: FakeLLMProvider) -> AnalysisService:
    return AnalysisService(
        redactor=SecretRedactor(),
        processor=LogProcessor(),
        classifier=FailureClassifier(),
        prompt_builder=PromptBuilder(),
        provider=provider,
    )


# ---------------------------------------------------------------------------
# the whole chain with the fake provider
# ---------------------------------------------------------------------------


async def test_full_chain_on_sample_log() -> None:
    provider = FakeLLMProvider()  # answers "authentication", matching the rules
    outcome = await _service(provider).analyze(SAMPLE.read_text())

    assert outcome.result.category is FailureCategory.AUTHENTICATION
    assert outcome.rule_based_category is FailureCategory.AUTHENTICATION
    assert outcome.category_agreement is True
    assert outcome.result.confidence == DEFAULT_FAKE_RESULT.confidence  # untouched on agreement
    assert "http_401" in outcome.matched_rules
    assert outcome.rule_based_severity == "high"
    assert outcome.severity_agreement is True  # the fake answers "high" too
    assert outcome.log_stats.secrets_redacted >= 1
    assert outcome.log_stats.total_lines > outcome.log_stats.excerpt_lines
    assert outcome.llm_provider == "fake"
    assert outcome.prompt_version == PROMPT_VERSION


async def test_the_provider_never_sees_the_secret() -> None:
    """The single most important property of the whole service."""
    provider = FakeLLMProvider()
    raw = SAMPLE.read_text()
    assert FAKE_ARTIFACTORY_KEY in raw

    await _service(provider).analyze(raw)

    assert len(provider.calls) == 1
    assert FAKE_ARTIFACTORY_KEY not in provider.calls[0].user
    assert FAKE_ARTIFACTORY_KEY not in provider.calls[0].system


async def test_disagreement_lowers_confidence_and_exposes_both_categories() -> None:
    model_says = DEFAULT_FAKE_RESULT.model_copy(
        update={"category": FailureCategory.NETWORK, "confidence": 0.9}
    )
    outcome = await _service(FakeLLMProvider(result=model_says)).analyze(SAMPLE.read_text())

    assert outcome.result.category is FailureCategory.NETWORK  # the model's opinion is kept
    assert outcome.rule_based_category is FailureCategory.AUTHENTICATION  # ...and so is ours
    assert outcome.category_agreement is False
    assert outcome.result.confidence == pytest.approx(0.63)  # 0.9 * 0.7


async def test_severity_disagreement_is_reported_but_not_penalised() -> None:
    model_says = DEFAULT_FAKE_RESULT.model_copy(update={"severity": "low", "confidence": 0.9})
    outcome = await _service(FakeLLMProvider(result=model_says)).analyze(SAMPLE.read_text())

    assert outcome.result.severity == "low"  # the model's judgement is kept
    assert outcome.rule_based_severity == "high"
    assert outcome.severity_agreement is False
    assert outcome.result.confidence == 0.9  # no penalty for severity


async def test_log_without_known_signature_gives_no_agreement_verdict() -> None:
    raw = "[INFO] step one\nsomething odd happened here\n[INFO] done\n"
    outcome = await _service(FakeLLMProvider()).analyze(raw)

    assert outcome.rule_based_category is FailureCategory.UNKNOWN
    assert outcome.category_agreement is None
    assert outcome.rule_based_severity is None
    assert outcome.severity_agreement is None
    assert outcome.result.confidence == DEFAULT_FAKE_RESULT.confidence


@pytest.mark.parametrize("raw", ["", "   \n  "])
async def test_empty_log_is_rejected_before_any_llm_call(raw: str) -> None:
    provider = FakeLLMProvider()

    with pytest.raises(ValueError):
        await _service(provider).analyze(raw)
    assert provider.calls == []


@pytest.mark.parametrize("error", [LLMUnavailableError("down"), LLMInvalidResponseError("garbage")])
async def test_provider_errors_propagate_unchanged(error: LLMError) -> None:
    with pytest.raises(type(error)):
        await _service(FakeLLMProvider(error=error)).analyze(SAMPLE.read_text())


# ---------------------------------------------------------------------------
# reconcile() on its own
# ---------------------------------------------------------------------------


def test_reconcile_keeps_result_when_rules_are_unknown() -> None:
    result, agreement = reconcile(DEFAULT_FAKE_RESULT, Classification(FailureCategory.UNKNOWN))

    assert result == DEFAULT_FAKE_RESULT
    assert agreement is None


def test_reconcile_agreement_is_untouched() -> None:
    classification = Classification(FailureCategory.AUTHENTICATION, ["http_401"])

    result, agreement = reconcile(DEFAULT_FAKE_RESULT, classification)

    assert result == DEFAULT_FAKE_RESULT
    assert agreement is True


def test_reconcile_disagreement_scales_confidence() -> None:
    classification = Classification(FailureCategory.COMPILATION, ["maven_compilation"])

    result, agreement = reconcile(DEFAULT_FAKE_RESULT, classification)

    assert agreement is False
    assert result.confidence == pytest.approx(DEFAULT_FAKE_RESULT.confidence * 0.7)
    assert result.category is DEFAULT_FAKE_RESULT.category
