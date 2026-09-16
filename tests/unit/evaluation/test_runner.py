from app.evaluation.dataset import load_cases
from app.evaluation.runner import EvalReport, format_report, run_case, run_offline
from app.services.failure_classifier import FailureCategory


def test_offline_run_on_the_default_set_is_clean() -> None:
    cases = load_cases()
    report = run_offline(cases)

    assert report.total >= 11
    assert report.key_phrase_rate == 1.0
    assert report.leaks == []


def test_hard_cases_pull_accuracy_below_one_and_are_marked_known() -> None:
    """A gauge that always reads 100% measures nothing: the set must contain rule misses."""
    cases = load_cases()
    report = run_offline(cases)
    gaps = [c for c in cases if c.rule_based_expected is not None]

    assert gaps, "the dataset needs cases the rules get wrong"
    assert report.category_accuracy == (report.total - len(gaps)) / report.total
    assert all(r.known_gap for r in report.results if not r.category_ok)
    assert "(known)" in format_report(report)


def test_a_wrong_label_lowers_accuracy_instead_of_failing() -> None:
    case = load_cases()[0]
    mislabelled = case.model_copy(update={"expected_category": FailureCategory.NETWORK})

    report = run_offline([case, mislabelled])

    assert report.category_accuracy == 0.5
    assert not report.results[1].category_ok


def test_severity_is_scored_separately_from_category() -> None:
    case = load_cases()[0]
    result = run_case(case)

    assert result.category_ok
    assert result.severity_ok == (result.got_severity == case.expected_severity)


def test_secret_that_survives_is_reported_as_leak() -> None:
    case = load_cases()[0]
    # pretend the planted secret were a harmless word that the excerpt does contain
    fake = case.model_copy(update={"secrets": [case.key_phrase]})

    report = run_offline([fake])

    assert report.leaks == report.results
    assert report.results[0].leaked_secrets == [case.key_phrase]


def test_report_is_readable_text() -> None:
    text = format_report(run_offline(load_cases()[:2]))

    assert "category accuracy: 100%" in text
    assert "secret leaks:      0" in text
    assert load_cases()[0].id in text


def test_empty_report_does_not_divide_by_zero() -> None:
    assert EvalReport().category_accuracy == 0.0
