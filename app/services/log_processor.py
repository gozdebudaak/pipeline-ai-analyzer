"""Log preprocessing.

Turns a raw CI/CD console log into a compact, analysis-ready form so the LLM
receives the failure and its context instead of thousands of lines of
download progress. Everything here is deterministic.

Stage 1 (``normalize``): clean and shrink the log without losing
information that matters.
Stage 2 (``extract``): locate error regions and keep them with surrounding
context, within a size budget. ``process`` runs both.
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
    duplicates_collapsed: int  # identical lines folded into a "(repeated N times)" marker
    similar_collapsed: int = 0  # lines differing only by digits folded into "(N similar lines)"

    @property
    def kept_lines(self) -> int:
        return len(self.lines)


class LogProcessor:
    def __init__(
        self,
        noise_patterns: tuple[re.Pattern[str], ...] = NOISE_PATTERNS,
        extraction: "ExtractionConfig | None" = None,
    ) -> None:
        self._noise_patterns = noise_patterns
        self._extraction = extraction or ExtractionConfig()

    def process(self, raw: str) -> "ProcessedLog":
        normalized = self.normalize(raw)
        excerpt, truncated = extract(normalized.lines, self._extraction)
        return ProcessedLog(
            excerpt=excerpt,
            error_lines=[line for line in normalized.lines if is_error_line(line)],
            warning_lines=[
                line
                for line in normalized.lines
                if is_warning_line(line) and not is_error_line(line)
            ],
            total_lines=normalized.total_lines,
            normalized_lines=normalized.kept_lines,
            excerpt_lines=len(excerpt.splitlines()) if excerpt else 0,
            truncated=truncated,
        )

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

        lines, duplicates_collapsed, similar_collapsed = _collapse_consecutive_duplicates(cleaned)

        return NormalizedLog(
            lines=lines,
            total_lines=len(raw_lines),
            noise_removed=noise_removed,
            duplicates_collapsed=duplicates_collapsed,
            similar_collapsed=similar_collapsed,
        )


_DIGITS = re.compile(r"\d+")


def _shape(line: str) -> str:
    """The line with every number replaced, so "module 185" and "module 186" compare equal."""
    return _DIGITS.sub("N", line)


def _collapse_consecutive_duplicates(lines: list[str]) -> tuple[list[str], int, int]:
    """Fold runs of identical lines, then runs of lines that differ only by digits.

    Retry loops print the same message hundreds of times; progress output
    prints the same message with a changing counter. The fact that it
    repeated is useful, the copies are not. Error lines are never folded as
    "similar": ``Failures: 0`` and ``Failures: 1`` have the same shape but
    opposite meaning, so only exact repeats apply to them.
    """
    result: list[str] = []
    identical = 0
    similar = 0
    i = 0
    while i < len(lines):
        line = lines[i]
        run = 1
        while i + run < len(lines) and lines[i + run] == line:
            run += 1
        if run > 1:
            result.append(f"{line}  (repeated {run} times)")
            identical += run - 1
            i += run
            continue

        shape = _shape(line)
        if not is_error_line(line):
            while (
                i + run < len(lines)
                and lines[i + run] != line
                and not is_error_line(lines[i + run])
                and _shape(lines[i + run]) == shape
            ):
                run += 1
        if run > 1:
            result.append(f"{line}  ({run} similar lines)")
            similar += run - 1
        else:
            result.append(line)
        i += run
    return result, identical, similar


# ---------------------------------------------------------------------------
# Stage 2: error extraction
# ---------------------------------------------------------------------------

# Lines that indicate a failure. A line is an error line if any pattern matches.
ERROR_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(?:ERROR|FATAL|SEVERE|CRITICAL|PANIC)\b"),
    re.compile(r"\b(?:BUILD FAILED|BUILD FAILURE|FAILURE|FAILED)\b"),
    re.compile(r"\b\w*(?:Exception|Error)\b(?::|\s+at\b|$)"),  # NullPointerException: / ValueError:
    re.compile(r"^Traceback \(most recent call last\)"),
    re.compile(r"^\s*(?:error|fatal|panic):", re.IGNORECASE),  # kubectl/git/docker "error: ..."
    re.compile(r"\bexit (?:code|status)[: =]+[1-9]\d*\b"),
    re.compile(
        r"\b(?:ImagePullBackOff|ErrImagePull|CrashLoopBackOff|OOMKilled|"
        r"FailedScheduling|FailedMount|FailedCreate|BackOff|Evicted)\b"
    ),
    re.compile(
        r"\b(?:Unauthorized|Forbidden|access denied|permission denied|"
        r"Permission denied|command not found|No such file or directory)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:status code|HTTP)[: ]+[45]\d\d\b|\b(?:401|403|404|500|502|503|504)\b.*"
        r"\b(?:Unauthorized|Forbidden|Not Found|Internal Server Error|Bad Gateway|"
        r"Service Unavailable|Gateway Timeout)\b"
    ),
    re.compile(r"\b(?:Could not|Cannot|Unable to|Failed to)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:timed out|timeout|Timeout|connection refused|Connection refused|"
        r"unreachable|no route to host)\b"
    ),
)

WARNING_PATTERNS: tuple[re.Pattern[str], ...] = (re.compile(r"\b(?:WARNING|WARN|Warning)\b"),)

_STACK_TRACE_LINE = re.compile(
    r"^(?:\s+at \S|Caused by: |\s+\.\.\. \d+ more|\s+File \".+\", line \d+|\s{4,}\S)"
)


def is_error_line(line: str) -> bool:
    return any(p.search(line) for p in ERROR_PATTERNS)


def is_warning_line(line: str) -> bool:
    return any(p.search(line) for p in WARNING_PATTERNS)


@dataclass(frozen=True)
class ExtractionConfig:
    context_before: int = 15  # the cause is usually printed before the error
    context_after: int = 5
    tail_lines: int = 20  # the build summary lives at the end of the log
    max_lines: int = 300
    max_chars: int = 12_000


@dataclass(frozen=True)
class ProcessedLog:
    excerpt: str
    error_lines: list[str]  # the matched error lines themselves, in order
    warning_lines: list[str]
    total_lines: int  # raw input
    normalized_lines: int  # after stage 1
    excerpt_lines: int  # what the LLM will see
    truncated: bool  # budget forced us to drop segments

    @property
    def first_error(self) -> str | None:
        return self.error_lines[0] if self.error_lines else None


@dataclass
class _Segment:
    start: int  # inclusive
    end: int  # exclusive
    anchor: int  # line index the segment was built around
    priority: int  # lower wins when the budget is tight

    @property
    def size(self) -> int:
        return self.end - self.start


_PRIORITY_TAIL = 0
_PRIORITY_FIRST_ERROR = 1
_PRIORITY_ERROR = 2
_PRIORITY_WARNING = 3

SKIP_MARKER = "... ({n} lines skipped) ..."


DEFAULT_EXTRACTION = ExtractionConfig()


def extract(lines: list[str], config: ExtractionConfig = DEFAULT_EXTRACTION) -> tuple[str, bool]:
    """Select error regions plus context from normalised lines.

    Returns the excerpt text and whether anything was dropped for budget.
    """
    n = len(lines)
    if n == 0:
        return "", False

    segments: list[_Segment] = []

    segments.append(_Segment(max(0, n - config.tail_lines), n, n - 1, _PRIORITY_TAIL))

    first_error_seen = False
    for i, line in enumerate(lines):
        if is_error_line(line):
            end = min(n, i + config.context_after + 1)
            while end < n and _STACK_TRACE_LINE.match(lines[end]):
                end += 1
            priority = _PRIORITY_ERROR if first_error_seen else _PRIORITY_FIRST_ERROR
            first_error_seen = True
            segments.append(_Segment(max(0, i - config.context_before), end, i, priority))
        elif is_warning_line(line):
            segments.append(_Segment(i, i + 1, i, _PRIORITY_WARNING))

    merged = _merge(segments)
    chosen, truncated = _fit_budget(merged, lines, config)
    return _render(chosen, lines), truncated


def _merge(segments: list[_Segment]) -> list[_Segment]:
    """Merge overlapping or adjacent segments; the merged one keeps the best priority."""
    ordered = sorted(segments, key=lambda s: s.start)
    merged: list[_Segment] = []
    for seg in ordered:
        if merged and seg.start <= merged[-1].end:
            last = merged[-1]
            if seg.priority < last.priority:
                last.anchor = seg.anchor
                last.priority = seg.priority
            last.end = max(last.end, seg.end)
        else:
            merged.append(_Segment(seg.start, seg.end, seg.anchor, seg.priority))
    return merged


def _fit_budget(
    segments: list[_Segment], lines: list[str], config: ExtractionConfig
) -> tuple[list[_Segment], bool]:
    """Keep whole segments in priority order while they fit the line/char budget."""
    remaining_lines = config.max_lines
    remaining_chars = config.max_chars
    chosen: list[_Segment] = []
    truncated = False

    for seg in sorted(segments, key=lambda s: (s.priority, s.start)):
        if seg.size == 0:
            continue
        seg_chars = sum(len(lines[j]) + 1 for j in range(seg.start, seg.end))
        if seg.size <= remaining_lines and seg_chars <= remaining_chars:
            chosen.append(seg)
            remaining_lines -= seg.size
            remaining_chars -= seg_chars
            continue

        truncated = True
        if seg.priority <= _PRIORITY_FIRST_ERROR:
            shrunk = _shrink_around_anchor(seg, lines, remaining_lines, remaining_chars)
            if shrunk is not None:
                chosen.append(shrunk)
                remaining_lines -= shrunk.size
                remaining_chars -= sum(len(lines[j]) + 1 for j in range(shrunk.start, shrunk.end))

    return sorted(chosen, key=lambda s: s.start), truncated


def _shrink_around_anchor(
    seg: _Segment, lines: list[str], max_lines: int, max_chars: int
) -> _Segment | None:
    start, end = seg.anchor, seg.anchor + 1
    chars = len(lines[seg.anchor]) + 1
    if max_lines < 1 or chars > max_chars:
        return None
    while True:
        grew = False
        for _ in range(2):
            if start > seg.start and (end - start) < max_lines:
                extra = len(lines[start - 1]) + 1
                if chars + extra <= max_chars:
                    start -= 1
                    chars += extra
                    grew = True
        if end < seg.end and (end - start) < max_lines:
            extra = len(lines[end]) + 1
            if chars + extra <= max_chars:
                end += 1
                chars += extra
                grew = True
        if not grew:
            break
    return _Segment(start, end, seg.anchor, seg.priority)


def _render(segments: list[_Segment], lines: list[str]) -> str:
    parts: list[str] = []
    cursor = 0
    for seg in segments:
        if seg.start > cursor:
            parts.append(SKIP_MARKER.format(n=seg.start - cursor))
        parts.extend(lines[seg.start : seg.end])
        cursor = seg.end
    if cursor < len(lines):
        parts.append(SKIP_MARKER.format(n=len(lines) - cursor))
    return "\n".join(parts)
