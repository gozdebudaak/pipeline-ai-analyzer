from dataclasses import replace

from app.evaluation.__main__ import main
from app.evaluation.calibration import (
    MIN_DISAGREEMENTS,
    format_factor_line,
    format_gap_table,
    gap_profile,
    suggest_disagreement_factor,
)
from app.evaluation.dataset import load_cases
from app.evaluation.runner import CaseResult, EvalReport, run_case
from app.services.failure_classifier import FailureCategory


def _with_llm(result: CaseResult, category: FailureCategory) -> CaseResult:
    return replace(result, llm_category=category, llm_severity="high", llm_confidence=0.9)


def _report(model_right: int, rules_right: int) -> EvalReport:
    """Synthetic disagreements: the label is the rule's category or the model's."""
    base = run_case(load_cases()[0])  # rules say authentication; make the model say network
    disagreeing = _with_llm(base, FailureCategory.NETWORK)
    model_wins = replace(disagreeing, expected_category=FailureCategory.NETWORK)
    rules_win = replace(disagreeing, expected_category=base.got_category)
    return EvalReport([model_wins] * model_right + [rules_win] * rules_right)


def test_factor_is_the_share_of_disagreements_the_model_won() -> None:
    assert suggest_disagreement_factor(_report(model_right=7, rules_right=3)) == 0.7
    assert suggest_disagreement_factor(_report(model_right=2, rules_right=8)) == 0.2


def test_too_few_disagreements_give_no_suggestion() -> None:
    assert suggest_disagreement_factor(_report(model_right=2, rules_right=2)) is None
    assert MIN_DISAGREEMENTS == 5


def test_factor_line_explains_itself() -> None:
    assert "measured 0.7 (model right in 7 of 10" in format_factor_line(_report(7, 3))
    assert "no suggestion yet (4 disagreements, need 5)" in format_factor_line(_report(2, 2))


def test_gap_profile_counts_markers_per_threshold() -> None:
    cases = {c.id: c for c in load_cases()}

    helm = gap_profile(cases["helm-upgrade-timeout"])  # one 5-minute wait for the rollout
    artifactory = gap_profile(cases["artifactory-503"])  # three 30-second connection timeouts

    assert helm.largest_gap == 300
    assert helm.markers_at[60] == 1 and helm.markers_at[300] == 1
    assert artifactory.largest_gap == 30
    assert artifactory.markers_at[30] == 3 and artifactory.markers_at[60] == 0


def test_gap_table_is_readable_and_totals_add_up() -> None:
    profiles = [gap_profile(c) for c in load_cases()]
    text = format_gap_table(profiles)

    assert ">=60s" in text and "total markers" in text
    assert "helm-upgrade-timeout" in text


def test_cli_gaps_mode(capsys) -> None:  # type: ignore[no-untyped-def]
    assert main(["--gaps"]) == 0
    assert "total markers" in capsys.readouterr().out
