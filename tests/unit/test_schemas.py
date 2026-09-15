import pytest
from pydantic import ValidationError

from app.schemas.analysis import AnalysisResult
from app.services.failure_classifier import FailureCategory

# The example response from the project specification, verbatim.
SPEC_EXAMPLE = {
    "status": "failed",
    "category": "dependency_resolution",
    "severity": "high",
    "summary": "The Maven build failed while downloading a dependency from Artifactory.",
    "root_cause": (
        "The pipeline received a 401 Unauthorized response while accessing the "
        "configured Artifactory repository."
    ),
    "confidence": 0.94,
    "evidence": ["Could not resolve dependencies for project", "401 Unauthorized"],
    "suggested_actions": [
        "Verify the Artifactory credentials configured in the pipeline.",
        "Check whether the service account has read permission for the repository.",
        "Verify that the configured repository URL is correct.",
    ],
}


def test_specification_example_is_valid() -> None:
    result = AnalysisResult.model_validate(SPEC_EXAMPLE)

    assert result.category is FailureCategory.DEPENDENCY_RESOLUTION
    assert result.confidence == 0.94
    assert len(result.suggested_actions) == 3


def test_round_trips_through_json() -> None:
    result = AnalysisResult.model_validate(SPEC_EXAMPLE)

    assert AnalysisResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [
        ("category", "something_new"),  # not in the enum
        ("severity", "urgent"),  # not one of the four levels
        ("status", "green"),
        ("confidence", 94),  # must be 0..1
        ("confidence", "high"),  # must be a number
        ("confidence", -0.1),
        ("summary", ""),  # empty
        ("summary", "x" * 501),  # too long
        ("root_cause", "   "),  # whitespace only -> stripped -> empty
        ("evidence", ["ok", ""]),  # empty fragment
        ("evidence", ["e"] * 11),  # too many
        ("suggested_actions", []),  # at least one action
        ("suggested_actions", "just a string"),  # must be a list
    ],
)
def test_invalid_values_are_rejected(field: str, bad_value: object) -> None:
    payload = {**SPEC_EXAMPLE, field: bad_value}

    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


@pytest.mark.parametrize(
    "missing",
    ["status", "category", "severity", "summary", "root_cause", "confidence", "suggested_actions"],
)
def test_missing_required_field_is_rejected(missing: str) -> None:
    payload = {k: v for k, v in SPEC_EXAMPLE.items() if k != missing}

    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


def test_evidence_is_optional_and_defaults_to_empty() -> None:
    payload = {k: v for k, v in SPEC_EXAMPLE.items() if k != "evidence"}

    assert AnalysisResult.model_validate(payload).evidence == []


def test_unknown_fields_are_rejected() -> None:
    payload = {**SPEC_EXAMPLE, "notes": "the model felt like adding this"}

    with pytest.raises(ValidationError):
        AnalysisResult.model_validate(payload)


def test_json_schema_lists_every_category() -> None:
    """The schema handed to the LLM must enumerate the allowed categories."""
    schema = AnalysisResult.model_json_schema()

    category_schema = schema["$defs"]["FailureCategory"]
    assert set(category_schema["enum"]) == {c.value for c in FailureCategory}
    assert schema["additionalProperties"] is False
