"""Log preprocessing.

Turns a raw CI/CD console log into a compact, analysis-ready form so the LLM
receives the failure and its context instead of thousands of lines of
download progress. Everything here is deterministic.

Stage 1 (``normalize``): clean and shrink the log without losing
information that matters.
Stage 2 (``extract``, next step): locate error regions and keep them with
surrounding context, within a size budget.
"""

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

# Terminal colour / cursor control sequences, e.g. "\x1b[31m" or "\x1b[0m".
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Leading timestamps in the common CI formats:
#   2026-09-15T09:06:10.532Z    [2026-09-15T09:06:10.532Z]    2026-09-15 09:06:10,532
_LEADING_TIMESTAMP = re.compile(
    r"^\[?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\]?\s*"
)

# Lines that never explain a failure. Each entry documents where it comes from.
NOISE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"^\s*$"),  # blank lines
    re.compile(r"^\[Pipeline\] "),  # Jenkins pipeline step markers
    re.compile(r"^(?:\[INFO\]\s*)?Download(?:ing|ed)(?: from)?[: ]"),  # Maven/Gradle downloads
    re.compile(r"^(?:\[INFO\]\s*)?Progress \(\d+\):"),  # Maven transfer progress
    re.compile(r"^\s*[\d.]+\s?[kKMG]?i?B\s*/\s*[\d.]+\s?[kKMG]?i?B"),  # "1.2 MB/4.5 MB" bars
    re.compile(r"^#\d+ (?:DONE|CACHED)\b"),  # Docker BuildKit finished/cached steps
    re.compile(r"^#\d+ sha256:[0-9a-f]{64}"),  # Docker BuildKit layer digests
    re.compile(r"^\s*---> [0-9a-f]{12}$"),  # legacy docker build intermediate ids
    re.compile(r"^(?:Get|Hit|Ign):\d+ https?://"),  # apt package index fetches
    re.compile(r"^(?:Collecting|Requirement already satisfied|Using cached)[: ]"),  # pip
    re.compile(r"^npm (?:WARN|notice) "),  # npm chatter
    re.compile(r"^\s*[─-╿▀-▟]+\s*$"),  # box-drawing / progress bar glyphs
)


def strip_ansi(line: str) -> str:
    return _ANSI_ESCAPE.sub("", line)


def strip_timestamp(line: str) -> str:
    return _LEADING_TIMESTAMP.sub("", line, count=1)


def is_noise(line: str) -> bool:
    return any(pattern.search(line) for pattern in NOISE_PATTERNS)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class NormalizedLog:
    lines: list[str]
    total_lines: int  # lines in the raw input
    noise_removed: int  # lines dropped as noise
    duplicates_collapsed: int  # lines folded into a "(repeated N times)" marker

    @property
    def kept_lines(self) -> int:
        return len(self.lines)


class LogProcessor:
    def __init__(self, noise_patterns: tuple[re.Pattern[str], ...] = NOISE_PATTERNS) -> None:
        self._noise_patterns = noise_patterns

    def normalize(self, raw: str) -> NormalizedLog:
        """Clean the raw log: strip escapes and timestamps, drop noise, fold repeats."""
        raw_lines = raw.splitlines()

        cleaned: list[str] = []
        noise_removed = 0
        for raw_line in raw_lines:
            line = strip_timestamp(strip_ansi(raw_line)).rstrip()
            if any(p.search(line) for p in self._noise_patterns):
                noise_removed += 1
                continue
            cleaned.append(line)

        lines, duplicates_collapsed = _collapse_consecutive_duplicates(cleaned)

        return NormalizedLog(
            lines=lines,
            total_lines=len(raw_lines),
            noise_removed=noise_removed,
            duplicates_collapsed=duplicates_collapsed,
        )


def _collapse_consecutive_duplicates(lines: list[str]) -> tuple[list[str], int]:
    """Fold runs of identical lines into one line with a repeat marker.

    Retry loops print the same message hundreds of times; the fact that it
    repeated is useful, the copies are not.
    """
    result: list[str] = []
    collapsed = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        run = 1
        while i + run < len(lines) and lines[i + run] == line:
            run += 1
        if run > 1:
            result.append(f"{line}  (repeated {run} times)")
            collapsed += run - 1
        else:
            result.append(line)
        i += run
    return result, collapsed
