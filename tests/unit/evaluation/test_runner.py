from app.evaluation.dataset import load_cases
from app.evaluation.runner import EvalReport, format_report, run_case, run_offline
from app.services.failure_classifier import FailureCategory


def test_offline_run_on_the_default_set_is_clean() -> None:
    cases = load_cases()
    report = run_offline(cases)

    assert report.total >= 11
    assert report.key_phrase_rate == 1.0
    assert report.leaks == []


def test_documented_gap_is_still_a_miss_but_marked_known() -> None:
    """A gap the label predicted lowers the score like any miss; the report just says so."""
    case = load_cases()[0]
    gap = case.model_copy(
        update={
            "expected_category": FailureCategory.NETWORK,  # pretend the human disagrees
            "rule_based_expected": case.expected_category,  # and documents what rules say
        }
    )

    report = run_offline([case, gap])

    assert report.category_accuracy == 0.5
    assert report.results[1].known_gap
    assert "(known)" in format_report(report)


def test_no_gap_is_documented_today() -> None:
    """Every hard case has a rule now; a new hard case must arrive with its gap documented."""
    report = run_offline(load_cases())

    assert report.category_accuracy == 1.0
    assert not any(r.known_gap for r in report.results)


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
