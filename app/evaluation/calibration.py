"""Turning guessed constants into measured ones.

Two numbers in the pipeline were chosen by feel:

* ``DISAGREEMENT_CONFIDENCE_FACTOR = 0.7`` (analysis_service): how much to trust
  the model when the rules disagree with it.
* ``gap_threshold_seconds = 60`` (log_processor): how long a pause between two
  log lines must be before it is worth pointing out to the model.

Neither can be derived from first principles; both can be measured against
the labelled set. This module does the measuring and prints the evidence.
The decision stays with a human.
"""

from dataclasses import dataclass

from app.evaluation.dataset import EvalCase
from app.evaluation.runner import EvalReport
from app.services.analysis_service import DISAGREEMENT_CONFIDENCE_FACTOR
from app.services.log_processor import parse_timestamp

MIN_DISAGREEMENTS = 5  # below this a ratio is noise, not evidence
DEFAULT_GAP_THRESHOLDS = (10, 30, 60, 120, 300)


def suggest_disagreement_factor(report: EvalReport) -> float | None:
    """P(model is right | rules disagree), the natural value for the confidence factor.

    The factor scales the model's own confidence in exactly that situation, so the
    observed share of disagreements the model won is the estimate. None until the
    report holds enough disagreements to mean anything.
    """
    disagreements = report.disagreements
    if len(disagreements) < MIN_DISAGREEMENTS:
        return None
    return round(report.who_was_right()["llm"] / len(disagreements), 2)


@dataclass(frozen=True)
class GapProfile:
    case_id: str
    largest_gap: int  # seconds between the two most distant consecutive stamped lines
    markers_at: dict[int, int]  # threshold -> how many "[gap]" markers it would insert


def gap_profile(case: EvalCase, thresholds: tuple[int, ...] = DEFAULT_GAP_THRESHOLDS) -> GapProfile:
    gaps: list[float] = []
    previous = None
    for line in case.read_log().splitlines():
        stamp = parse_timestamp(line)
        if stamp is None:
            continue
        if previous is not None:
            gaps.append((stamp - previous).total_seconds())
        previous = stamp
    return GapProfile(
        case_id=case.id,
        largest_gap=int(max(gaps, default=0)),
        markers_at={t: sum(g >= t for g in gaps) for t in thresholds},
    )


def format_gap_table(
    profiles: list[GapProfile], thresholds: tuple[int, ...] = DEFAULT_GAP_THRESHOLDS
) -> str:
    """How many markers each candidate threshold would insert per log.

    A good threshold marks the pauses a human would call "it hung here" (a 5 minute
    helm wait, a 30 second retry loop) and stays silent on routine spacing.
    """
    head = f"{'case':<24} {'largest':>8}  " + "  ".join(f"{f'>={t}s':>6}" for t in thresholds)
    rows = [
        f"{p.case_id:<24} {p.largest_gap:>7}s  "
        + "  ".join(f"{p.markers_at[t]:>6}" for t in thresholds)
        for p in profiles
    ]
    totals = {t: sum(p.markers_at[t] for p in profiles) for t in thresholds}
    footer = f"{'total markers':<24} {'':>8}  " + "  ".join(f"{totals[t]:>6}" for t in thresholds)
    return "\n".join([head, *rows, "", footer])


def format_factor_line(report: EvalReport) -> str:
    suggested = suggest_disagreement_factor(report)
    current = DISAGREEMENT_CONFIDENCE_FACTOR
    if suggested is None:
        n = len(report.disagreements)
        return (
            f"confidence factor: current {current}, no suggestion yet "
            f"({n} disagreements, need {MIN_DISAGREEMENTS})"
        )
    won = report.who_was_right()["llm"]
    return (
        f"confidence factor: current {current}, measured {suggested} "
        f"(model right in {won} of {len(report.disagreements)} disagreements)"
    )
