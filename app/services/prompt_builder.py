"""Builds the messages sent to the LLM.

The prompt is the most important string in the application, so it lives in
one place, is versioned with the code and is covered by tests.

Design decision: the rule-based classification is deliberately *not* given
to the model. Keeping the two opinions independent lets the analysis
service use agreement between them as a confidence signal and show both to
the engineer. Telling the model the answer would make agreement meaningless.
"""

from dataclasses import dataclass

from app.services.log_processor import SKIP_MARKER
from app.services.secret_redactor import REDACTED

PROMPT_VERSION = "1"

SYSTEM_PROMPT = f"""You are a senior DevOps engineer analysing a failed CI/CD pipeline run.

You will receive an excerpt of the pipeline log. Your job is to explain what
failed, why it most probably failed, and what the engineer should do next.

Rules:
1. Base every conclusion on the log excerpt only. Do not invent details that
   are not in the log.
2. Quote evidence verbatim from the excerpt. Evidence must be short fragments
   of actual log lines.
3. Secrets were removed before you saw the log and appear as "{REDACTED}".
   A "{REDACTED}" marker is never itself the cause of a failure; treat it as an
   ordinary value that was present.
4. Lines shown as "{SKIP_MARKER.format(n="N")}" were omitted for brevity. Do not
   assume the lines around a skip marker are consecutive.
5. Prefer the earliest error that explains the later ones: later errors are
   often consequences of the first one.
6. Suggested actions must be concrete checks or changes an engineer can
   perform, ordered from most to least likely to resolve the failure.
7. Calibrate confidence honestly: use values below 0.5 when the excerpt does
   not clearly show the cause, and choose the category "unknown" if none of
   the known categories fits.

Respond with a single JSON object that matches the provided schema exactly.
Do not include any text before or after the JSON.
"""

USER_PROMPT_TEMPLATE = """Analyse the following CI/CD pipeline log excerpt.

<log_excerpt>
{excerpt}
</log_excerpt>
"""


@dataclass(frozen=True)
class Prompt:
    system: str
    user: str
    version: str = PROMPT_VERSION


class PromptBuilder:
    def build(self, excerpt: str) -> Prompt:
        """Wrap a redacted, preprocessed log excerpt into system + user messages."""
        if not excerpt.strip():
            raise ValueError("cannot build a prompt from an empty log excerpt")
        return Prompt(system=SYSTEM_PROMPT, user=USER_PROMPT_TEMPLATE.format(excerpt=excerpt))
