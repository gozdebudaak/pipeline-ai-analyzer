"""Run the labelled dataset through the deterministic chain and score it.

Offline mode: redactor -> log processor -> rule-based classifier, no LLM. It
answers "how good are the rules?" in seconds and for free, so it can run on
every change. Scores are percentages, not pass/fail: a drop is information,
not necessarily a bug. The one binary thing is a leaked secret.

LLM mode adds the model's answer next to the rules' answer for every case.
That is the only way to learn what the 0.7 disagreement factor should be:
when the two disagree, who is right, and how often?
"""

from dataclasses import dataclass, field, replace

from app.evaluation.dataset import EvalCase
from app.llm.base import LLMError
from app.services.analysis_service import AnalysisService
from app.services.failure_classifier import FailureCategory, FailureClassifier, Severity
from app.services.log_processor import LogProcessor
from app.services.secret_redactor import SecretRedactor


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    expected_category: FailureCategory
    got_category: FailureCategory
    known_gap: bool  # the label predicted this miss; still a miss in the score
    expected_severity: Severity
    got_severity: Severity | None
    matched_rules: list[str]
    key_phrase_found: bool
    leaked_secrets: list[str]
    excerpt_tokens: int
    llm_category: FailureCategory | None = None
    llm_severity: Severity | None = None
    llm_confidence: float | None = None
    llm_input_tokens: int | None = None
    llm_error: str | None = None

    @property
    def category_ok(self) -> bool:
        return self.got_category is self.expected_category

    @property
    def severity_ok(self) -> bool:
        return self.got_severity == self.expected_severity

    @property
    def has_llm_answer(self) -> bool:
        return self.llm_category is not None

    @property
    def llm_category_ok(self) -> bool:
        return self.llm_category is self.expected_category

    @property
    def llm_severity_ok(self) -> bool:
        return self.llm_severity == self.expected_severity

    @property
    def disagreement(self) -> bool:
        return self.has_llm_answer and self.llm_category is not self.got_category


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

    @property
    def answered_by_llm(self) -> list[CaseResult]:
        return [r for r in self.results if r.has_llm_answer]

    @property
    def llm_errors(self) -> list[CaseResult]:
        return [r for r in self.results if r.llm_error is not None]

    @property
    def llm_category_accuracy(self) -> float:
        answered = self.answered_by_llm
        return sum(r.llm_category_ok for r in answered) / len(answered) if answered else 0.0

    @property
    def llm_severity_accuracy(self) -> float:
        answered = self.answered_by_llm
        return sum(r.llm_severity_ok for r in answered) / len(answered) if answered else 0.0

    @property
    def disagreements(self) -> list[CaseResult]:
        return [r for r in self.results if r.disagreement]

    def who_was_right(self) -> dict[str, int]:
        """On disagreement: the evidence behind the confidence factor."""
        counts = {"llm": 0, "rules": 0, "neither": 0}
        for r in self.disagreements:
            if r.llm_category_ok:
                counts["llm"] += 1
            elif r.category_ok:
                counts["rules"] += 1
            else:
                counts["neither"] += 1
        return counts

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
        known_gap=classification.category is case.rule_based_expected,
        expected_severity=case.expected_severity,
        got_severity=classification.severity,
        matched_rules=classification.matched_rules,
        key_phrase_found=case.key_phrase in processed.excerpt,
        leaked_secrets=[s for s in case.secrets if s in processed.excerpt],
        excerpt_tokens=processed.estimated_tokens,
    )


def run_offline(cases: list[EvalCase]) -> EvalReport:
    return EvalReport([run_case(case) for case in cases])


async def run_case_with_llm(case: EvalCase, service: AnalysisService) -> CaseResult:
    base = run_case(case)
    try:
        outcome = await service.analyze(case.read_log())
    except LLMError as exc:
        return replace(base, llm_error=f"{type(exc).__name__}: {exc}")
    return replace(
        base,
        llm_category=outcome.result.category,
        llm_severity=outcome.result.severity,
        llm_confidence=outcome.result.confidence,
        llm_input_tokens=outcome.input_tokens,
    )


async def run_with_llm(cases: list[EvalCase], service: AnalysisService) -> EvalReport:
    return EvalReport([await run_case_with_llm(case, service) for case in cases])


def _llm_columns(r: CaseResult) -> str:
    if r.llm_error is not None:
        return f"{'ERROR':<24} {'-':<9} {'-':>5}  "
    if not r.has_llm_answer:
        return ""
    cat = "ok" if r.llm_category_ok else f"{r.llm_category}"
    sev = "ok" if r.llm_severity_ok else f"{r.llm_severity}"
    return f"{cat:<24} {sev:<9} {r.llm_confidence:>5.2f}  "


def format_report(report: EvalReport) -> str:
    """One line per case, then the totals. Plain text so it reads in CI logs."""
    llm_mode = any(r.has_llm_answer or r.llm_error is not None for r in report.results)
    llm_head = f"{'llm category':<24} {'llm sev':<9} {'conf':>5}  " if llm_mode else ""
    lines = [
        f"{'case':<24} {'category':<30} {'severity':<9} {'phrase':<7} {'tokens':>6}  "
        f"{llm_head}rules"
    ]
    for r in report.results:
        cat = "ok" if r.category_ok else f"{r.got_category}" + (" (known)" if r.known_gap else "")
        sev = "ok" if r.severity_ok else f"{r.got_severity}"
        phrase = "ok" if r.key_phrase_found else "MISSING"
        lines.append(
            f"{r.case_id:<24} {cat:<30} {sev:<9} {phrase:<7} {r.excerpt_tokens:>6}  "
            f"{_llm_columns(r) if llm_mode else ''}{', '.join(r.matched_rules) or '-'}"
        )
    lines.append("")
    lines.append(f"cases:             {report.total}")
    lines.append(f"category accuracy: {report.category_accuracy:.0%}")
    lines.append(f"severity accuracy: {report.severity_accuracy:.0%}")
    lines.append(f"key phrase kept:   {report.key_phrase_rate:.0%}")
    lines.append(f"secret leaks:      {len(report.leaks)}")
    for r in report.leaks:
        lines.append(f"  LEAK in {r.case_id}: {r.leaked_secrets}")
    if llm_mode:
        answered, errors = report.answered_by_llm, report.llm_errors
        who = report.who_was_right()
        lines.append("")
        lines.append(f"llm answered:      {len(answered)} (errors: {len(errors)})")
        lines.append(f"llm category acc:  {report.llm_category_accuracy:.0%}")
        lines.append(f"llm severity acc:  {report.llm_severity_accuracy:.0%}")
        lines.append(
            f"disagreements:     {len(report.disagreements)} "
            f"(llm right {who['llm']}, rules right {who['rules']}, neither {who['neither']})"
        )
        tokens = sum(r.llm_input_tokens or 0 for r in answered)
        lines.append(f"llm input tokens:  {tokens}")
    return "\n".join(lines)
