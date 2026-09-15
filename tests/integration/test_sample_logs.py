"""Run the three deterministic services as a chain over realistic sample logs.

Each sample log mimics real tool output and deliberately embeds secrets. For
every file we check the whole chain: secrets are gone, the failure is found,
the category is right, and the log actually shrank.
"""

from pathlib import Path

import pytest

from app.services.failure_classifier import FailureCategory, FailureClassifier
from app.services.log_processor import LogProcessor
from app.services.secret_redactor import SecretRedactor

SAMPLE_LOGS = Path(__file__).resolve().parents[2] / "sample_logs"

CASES = [
    pytest.param(
        "maven/dependency_resolution_401.log",
        FailureCategory.AUTHENTICATION,
        ["AKCp8k3Jd9sLx2mQw7vB4nH6tY1uZ0pR"],
        "Could not resolve dependencies",
        id="maven-401",
    ),
    pytest.param(
        "docker/build_failed_missing_file.log",
        FailureCategory.CONTAINER_BUILD,
        ["ghp_Xk92JqLmN3vP8sT4wR7yB1zC5dF6gH0aE2iU"],
        "failed to solve",
        id="docker-missing-file",
    ),
    pytest.param(
        "kubernetes/image_pull_backoff.log",
        FailureCategory.KUBERNETES_DEPLOYMENT,
        ["QmFzZTY0U2lnbmF0dXJlR29lc0hlcmVBbmRJdElzTG9uZw"],
        "ImagePullBackOff",
        id="k8s-image-pull-backoff",
    ),
    pytest.param(
        "artifactory/upload_forbidden_403.log",
        FailureCategory.AUTHORIZATION,
        ["AKCp8k3Jd9sLx2mQw7vB4nH6tY1uZ0pR"],
        "lacks deploy permission",
        id="artifactory-403",
    ),
]


@pytest.mark.parametrize(("relative_path", "category", "secrets", "key_phrase"), CASES)
def test_chain_on_sample_log(
    relative_path: str, category: FailureCategory, secrets: list[str], key_phrase: str
) -> None:
    raw = (SAMPLE_LOGS / relative_path).read_text()
    for secret in secrets:
        assert secret in raw, "the sample must actually contain the secret"

    redacted = SecretRedactor().redact(raw)
    processed = LogProcessor().process(redacted.text)
    classification = FailureClassifier().classify(processed.error_lines)

    # 1. nothing sensitive survives to the text that would reach the LLM
    for secret in secrets:
        assert secret not in processed.excerpt
    assert redacted.total >= 1

    # 2. the failure and its key evidence are in the excerpt
    assert processed.error_lines, "no error lines detected"
    assert key_phrase in processed.excerpt

    # 3. deterministic classification agrees with a human reading
    assert classification.category is category

    # 4. preprocessing removed something (noise and/or duplicates)
    assert processed.normalized_lines < processed.total_lines
    assert not processed.truncated


def test_every_sample_log_is_covered() -> None:
    """Adding a sample log without a test case must fail loudly."""
    on_disk = {str(p.relative_to(SAMPLE_LOGS)) for p in SAMPLE_LOGS.rglob("*.log")}
    covered = {case.values[0] for case in CASES}

    assert on_disk == covered
