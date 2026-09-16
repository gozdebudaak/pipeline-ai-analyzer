"""Run the three deterministic services as a chain over realistic sample logs.

Each sample log mimics real tool output and deliberately embeds secrets. For
every file we check the whole chain: secrets are gone, the failure is found,
the category is right, and the log actually shrank.

The expected answers live in evaluation/cases.json (see app.evaluation.dataset),
shared with ``make eval`` so tests and evaluation can never disagree.
"""

import pytest

from app.evaluation.dataset import EvalCase, load_cases
from app.services.failure_classifier import FailureClassifier
from app.services.log_processor import LogProcessor
from app.services.secret_redactor import SecretRedactor

CASES = [pytest.param(case, id=case.id) for case in load_cases()]


@pytest.mark.parametrize("case", CASES)
def test_chain_on_sample_log(case: EvalCase) -> None:
    raw = case.read_log()
    secrets, key_phrase, category = case.secrets, case.key_phrase, case.expected_category

    redacted = SecretRedactor().redact(raw)
    processed = LogProcessor().process(redacted.text)
    classification = FailureClassifier().classify(processed.error_lines)

    # 1. nothing sensitive survives. redacted.text is the superset (strongest check);
    #    excerpt is the boundary: the exact text that would reach the LLM.
    for secret in secrets:
        assert secret not in redacted.text
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
