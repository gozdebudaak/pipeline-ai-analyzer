from app.evaluation.__main__ import main
from app.evaluation.dataset import load_cases
from app.evaluation.runner import format_report, run_with_llm
from app.llm.base import LLMUnavailableError
from app.llm.fake import DEFAULT_FAKE_RESULT, FakeLLMProvider
from app.services.analysis_service import AnalysisService
from app.services.failure_classifier import FailureClassifier
from app.services.log_processor import LogProcessor
from app.services.prompt_builder import PromptBuilder
from app.services.secret_redactor import SecretRedactor


def _service(provider: FakeLLMProvider) -> AnalysisService:
    return AnalysisService(
        redactor=SecretRedactor(),
        processor=LogProcessor(),
        classifier=FailureClassifier(),
        prompt_builder=PromptBuilder(),
        provider=provider,
    )


def _by_id(*ids: str) -> list:  # type: ignore[type-arg]
    cases = {c.id: c for c in load_cases()}
    return [cases[i] for i in ids]


async def test_llm_answer_is_scored_next_to_the_rules() -> None:
    # The fake always answers authentication/high: right for npm-401, wrong for gradle.
    report = await run_with_llm(
        _by_id("npm-401", "gradle-test-failure"), _service(FakeLLMProvider())
    )

    npm, gradle = report.results
    assert npm.llm_category is DEFAULT_FAKE_RESULT.category
    assert npm.llm_category_ok and not gradle.llm_category_ok
    assert report.llm_category_accuracy == 0.5
    assert report.category_accuracy == 1.0  # the rules' score is untouched by the model


async def test_disagreements_record_who_was_right() -> None:
    report = await run_with_llm(
        _by_id("npm-401", "gradle-test-failure"), _service(FakeLLMProvider())
    )

    assert [r.case_id for r in report.disagreements] == ["gradle-test-failure"]
    assert report.who_was_right() == {"llm": 0, "rules": 1, "neither": 0}


async def test_provider_failure_is_recorded_not_raised() -> None:
    provider = FakeLLMProvider(error=LLMUnavailableError("quota exceeded"))

    report = await run_with_llm(_by_id("npm-401"), _service(provider))

    (result,) = report.results
    assert result.llm_error == "LLMUnavailableError: quota exceeded"
    assert not result.has_llm_answer
    assert report.answered_by_llm == [] and len(report.llm_errors) == 1
    assert result.category_ok  # the offline part of the case still scored


async def test_every_case_reaches_the_model_exactly_once_and_redacted() -> None:
    provider = FakeLLMProvider()
    cases = load_cases()

    await run_with_llm(cases, _service(provider))

    assert len(provider.calls) == len(cases)
    for case, prompt in zip(cases, provider.calls, strict=True):
        for secret in case.secrets:
            assert secret not in prompt.user
            assert secret not in prompt.system


async def test_report_shows_llm_columns_only_in_llm_mode() -> None:
    cases = _by_id("npm-401")
    text = format_report(await run_with_llm(cases, _service(FakeLLMProvider())))

    assert "llm category" in text
    assert "disagreements:     0" in text
    assert "llm input tokens:  100" in text


def test_cli_llm_mode_uses_the_fake_provider_under_test_env(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--llm"]) == 0
    assert "llm category acc:" in capsys.readouterr().out
