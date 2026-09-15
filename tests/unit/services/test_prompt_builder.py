import pytest

from app.services.log_processor import SKIP_MARKER
from app.services.prompt_builder import PROMPT_VERSION, PromptBuilder
from app.services.secret_redactor import REDACTED

EXCERPT = (
    "[ERROR] Failed to execute goal on project payment-service\n"
    f"{SKIP_MARKER.format(n=40)}\n"
    "[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.\n"
    "[INFO] BUILD FAILURE"
)


def test_excerpt_is_embedded_verbatim() -> None:
    prompt = PromptBuilder().build(EXCERPT)

    assert EXCERPT in prompt.user
    assert "<log_excerpt>" in prompt.user


def test_system_prompt_explains_redaction_and_skip_markers() -> None:
    prompt = PromptBuilder().build(EXCERPT)

    assert REDACTED in prompt.system
    assert SKIP_MARKER.format(n="N") in prompt.system


def test_system_prompt_demands_json_only() -> None:
    prompt = PromptBuilder().build(EXCERPT)

    assert "JSON" in prompt.system
    assert "schema" in prompt.system


def test_rule_based_classification_is_not_leaked_into_the_prompt() -> None:
    """Design decision: the model must form an independent opinion."""
    prompt = PromptBuilder().build(EXCERPT)

    for word in ("rule-based", "classifier", "pre-classified", "our classification"):
        assert word not in prompt.system.lower()
        assert word not in prompt.user.lower()


def test_prompt_is_deterministic() -> None:
    builder = PromptBuilder()

    assert builder.build(EXCERPT) == builder.build(EXCERPT)


def test_prompt_carries_a_version() -> None:
    assert PromptBuilder().build(EXCERPT).version == PROMPT_VERSION


@pytest.mark.parametrize("empty", ["", "   ", "\n\n"])
def test_empty_excerpt_is_rejected(empty: str) -> None:
    with pytest.raises(ValueError):
        PromptBuilder().build(empty)
