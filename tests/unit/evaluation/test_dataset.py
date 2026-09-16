import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.evaluation.dataset import SAMPLE_LOGS_DIR, EvalCase, load_cases
from app.services.failure_classifier import FailureCategory


def test_default_dataset_loads_and_is_labelled() -> None:
    cases = load_cases()

    assert len(cases) >= 8
    assert all(case.expected_category is not FailureCategory.UNKNOWN for case in cases)


def test_every_sample_log_has_a_case() -> None:
    """Adding a sample log without a label must fail loudly."""
    on_disk = {str(p.relative_to(SAMPLE_LOGS_DIR)) for p in SAMPLE_LOGS_DIR.rglob("*.log")}
    labelled = {case.log for case in load_cases()}

    assert on_disk == labelled


def test_planted_secrets_really_are_in_the_logs() -> None:
    for case in load_cases():
        raw = case.read_log()
        for secret in case.secrets:
            assert secret in raw, f"{case.id}: {secret!r} not in {case.log}"


def _write(tmp_path: Path, items: list[dict[str, object]]) -> Path:
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(items))
    return path


def _valid() -> dict[str, object]:
    return {
        "id": "x",
        "log": "maven/dependency_resolution_401.log",
        "expected_category": "authentication",
        "expected_severity": "high",
        "key_phrase": "401",
        "secrets": ["AKCp8k3Jd9sLx2mQw7vB4nH6tY1uZ0pR"],
    }


def test_duplicate_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="duplicate case ids"):
        load_cases(_write(tmp_path, [_valid(), _valid()]))


def test_missing_log_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="sample logs not found"):
        load_cases(_write(tmp_path, [{**_valid(), "log": "nope/missing.log"}]))


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("expected_category", "disk_full"),  # not one of the 13
        ("expected_severity", "urgent"),
        ("secrets", []),
        ("key_phrase", ""),
    ],
)
def test_bad_labels_are_rejected(field: str, bad_value: object) -> None:
    with pytest.raises(ValidationError):
        EvalCase.model_validate({**_valid(), field: bad_value})


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        EvalCase.model_validate({**_valid(), "note": "typo in a label name must not pass"})
