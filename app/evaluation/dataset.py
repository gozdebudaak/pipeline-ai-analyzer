"""The labelled evaluation set.

Tests ask "does it work?" and answer yes/no. Evaluation asks "how well?" and
answers with a number that we track as rules, prompts and models change. Both
need the same thing: sample logs with a human-written expected answer. This
module is the single source of truth for those labels; the integration tests
and ``make eval`` both read it.

One case = one sample log + what a careful engineer would say about it.
"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from app.services.failure_classifier import FailureCategory, Severity

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASES_PATH = REPO_ROOT / "evaluation" / "cases.json"
SAMPLE_LOGS_DIR = REPO_ROOT / "sample_logs"


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    log: str = Field(description="Path relative to sample_logs/")
    expected_category: FailureCategory
    expected_severity: Severity
    # A documented gap: what the rules return today when they are known to disagree with
    # the human label. The integration test pins this value (a rule change that closes the
    # gap must update the label); the evaluation still scores against expected_category.
    rule_based_expected: FailureCategory | None = None
    key_phrase: str = Field(min_length=1, description="Must survive into the excerpt")
    secrets: list[str] = Field(min_length=1, description="Planted values that must not leak")

    @property
    def category_the_rules_should_return(self) -> FailureCategory:
        return self.rule_based_expected or self.expected_category

    @property
    def log_path(self) -> Path:
        return SAMPLE_LOGS_DIR / self.log

    def read_log(self) -> str:
        return self.log_path.read_text()


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[EvalCase]:
    """Parse and validate the dataset; a bad label fails here, not deep inside a run."""
    cases = [EvalCase.model_validate(item) for item in json.loads(path.read_text())]

    ids = [case.id for case in cases]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise ValueError(f"duplicate case ids: {sorted(duplicates)}")

    missing = [case.log for case in cases if not case.log_path.is_file()]
    if missing:
        raise ValueError(f"sample logs not found: {missing}")

    return cases
