"""Run the labelled dataset through the deterministic chain and score it.

Offline mode: redactor -> log processor -> rule-based classifier, no LLM. It
answers "how good are the rules?" in seconds and for free, so it can run on
every change. Scores are percentages, not pass/fail: a drop is information,
not necessarily a bug. The one binary thing is a leaked secret.
"""

from dataclasses import dataclass, field

from app.evaluation.dataset import EvalCase
from app.services.failure_classifier import FailureCategory, FailureClassifier, Severity
from app.services.log_processor import LogProcessor
from app.services.secret_redactor import SecretRedactor


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    expected_category: FailureCategory
    got_category: FailureCategory
    expected_severity: Severity
    got_severity: Severity | None
    matched_rules: list[str]
    key_phrase_found: bool
    leaked_secrets: list[str]
    excerpt_tokens: int

    @property
    def category_ok(self) -> bool:
        return self.got_category is self.expected_category

    @property
    def severity_ok(self) -> bool:
        return self.got_severity == self.expected_severity


@dataclass(frozen=True)
class EvalReport:
    results: list[CaseResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def category_accuracy(self) -> float:
        return self._rate(sum(r.category_ok for r in self.results))

    @property
    def severity_accuracy(self) -> float:
        return self._rate(sum(r.severity_ok for r in self.results))

    @property
    def key_phrase_rate(self) -> float:
        return self._rate(sum(r.key_phrase_found for r in self.results))

    @property
    def leaks(self) -> list[CaseResult]:
        return [r for r in self.results if r.leaked_secrets]

    def _rate(self, hits: int) -> float:
        return hits / self.total if self.total else 0.0


def run_case(case: EvalCase) -> CaseResult:
    redacted = SecretRedactor().redact(case.read_log())
    processed = LogProcessor().process(redacted.text)
    classification = FailureClassifier().classify(processed.error_lines)
    return CaseResult(
        case_id=case.id,
        expected_category=case.expected_category,
        got_category=classification.category,
        expected_severity=case.expected_severity,
        got_severity=classification.severity,
        matched_rules=classification.matched_rules,
        key_phrase_found=case.key_phrase in processed.excerpt,
        leaked_secrets=[s for s in case.secrets if s in processed.excerpt],
        excerpt_tokens=processed.estimated_tokens,
    )


def run_offline(cases: list[EvalCase]) -> EvalReport:
    return EvalReport([run_case(case) for case in cases])


def format_report(report: EvalReport) -> str:
    """One line per case, then the totals. Plain text so it reads in CI logs."""
    lines = [f"{'case':<24} {'category':<9} {'severity':<9} {'phrase':<7} {'tokens':>6}  rules"]
    for r in report.results:
        cat = "ok" if r.category_ok else f"{r.got_category}"
        sev = "ok" if r.severity_ok else f"{r.got_severity}"
        phrase = "ok" if r.key_phrase_found else "MISSING"
        lines.append(
            f"{r.case_id:<24} {cat:<9} {sev:<9} {phrase:<7} {r.excerpt_tokens:>6}  "
            f"{', '.join(r.matched_rules) or '-'}"
        )
    lines.append("")
    lines.append(f"cases:             {report.total}")
    lines.append(f"category accuracy: {report.category_accuracy:.0%}")
    lines.append(f"severity accuracy: {report.severity_accuracy:.0%}")
    lines.append(f"key phrase kept:   {report.key_phrase_rate:.0%}")
    lines.append(f"secret leaks:      {len(report.leaks)}")
    for r in report.leaks:
        lines.append(f"  LEAK in {r.case_id}: {r.leaked_secrets}")
    return "\n".join(lines)
