import pytest

from app.services.log_processor import (
    GAP_MARKER,
    LogProcessor,
    is_gap_marker,
    is_noise,
    parse_timestamp,
    strip_ansi,
    strip_timestamp,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_strip_ansi_removes_colour_codes() -> None:
    assert strip_ansi("\x1b[1;31m[ERROR]\x1b[0m Build failed") == "[ERROR] Build failed"


def test_strip_ansi_leaves_plain_text_alone() -> None:
    assert strip_ansi("[INFO] BUILD SUCCESS") == "[INFO] BUILD SUCCESS"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("2026-09-15T09:06:10.532Z [INFO] start", "[INFO] start"),
        ("[2026-09-15T09:06:10.532Z] [INFO] start", "[INFO] start"),
        ("2026-09-15 09:06:10,532 INFO start", "INFO start"),
        ("2026-09-15T09:06:10+03:00 start", "start"),
        ("[INFO] no timestamp here", "[INFO] no timestamp here"),
    ],
)
def test_strip_timestamp(line: str, expected: str) -> None:
    assert strip_timestamp(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "",
        "   ",
        "[Pipeline] { (Build)",
        "[INFO] Downloading from central: https://repo.maven.apache.org/maven2/x.pom",
        "[INFO] Downloaded from central: https://repo.maven.apache.org/maven2/x.pom (2.1 kB)",
        "Progress (1): 45/120 kB",
        "#12 DONE 0.4s",
        "#7 CACHED",
        "Get:3 http://deb.debian.org/debian bookworm/main arm64 Packages [8,000 kB]",
        "Requirement already satisfied: idna in ./.venv/lib",
        "npm WARN deprecated request@2.88.2",
        "> Task :processResources UP-TO-DATE",
    ],
)
def test_noise_lines_are_detected(line: str) -> None:
    assert is_noise(line)


@pytest.mark.parametrize(
    "line",
    [
        "[ERROR] Failed to execute goal on project payment-service",
        "Step 5/12 : RUN mvn -B package",
        '#12 ERROR: process "/bin/sh -c mvn package" did not complete successfully',
        "Error response from daemon: pull access denied for registry.example.com/app",
        "Warning: FailedScheduling  0/3 nodes are available",
    ],
)
def test_signal_lines_are_not_noise(line: str) -> None:
    assert not is_noise(line)


# ---------------------------------------------------------------------------
# normalize() as a whole
# ---------------------------------------------------------------------------


def test_normalize_cleans_and_counts() -> None:
    raw = (
        "[2026-09-15T09:00:00.000Z] [Pipeline] { (Build)\n"
        "[2026-09-15T09:00:01.000Z] [INFO] Downloading from central: https://x/a.pom\n"
        "[2026-09-15T09:00:02.000Z] \x1b[31m[ERROR]\x1b[0m Failed to execute goal\n"
        "\n"
        "[2026-09-15T09:00:03.000Z] [ERROR] BUILD FAILURE\n"
    )

    result = LogProcessor().normalize(raw)

    assert result.lines == ["[ERROR] Failed to execute goal", "[ERROR] BUILD FAILURE"]
    assert result.total_lines == 5
    assert result.noise_removed == 3
    assert result.kept_lines == 2


def test_consecutive_duplicates_are_collapsed_with_count() -> None:
    raw = "Waiting for database...\n" * 300 + "Connected.\n"

    result = LogProcessor().normalize(raw)

    assert result.lines == ["Waiting for database...  (repeated 300 times)", "Connected."]
    assert result.duplicates_collapsed == 299


def test_duplicates_are_detected_even_when_timestamps_differ() -> None:
    raw = (
        "2026-09-15T09:00:00Z Retrying connection\n"
        "2026-09-15T09:00:01Z Retrying connection\n"
        "2026-09-15T09:00:02Z Retrying connection\n"
    )

    result = LogProcessor().normalize(raw)

    assert result.lines == ["Retrying connection  (repeated 3 times)"]


def test_lines_differing_only_by_digits_are_folded_as_similar() -> None:
    raw = "\n".join(f"[INFO] compiled module {i}" for i in range(185, 200)) + "\n[INFO] done\n"

    result = LogProcessor().normalize(raw)

    assert result.lines == ["[INFO] compiled module 185  (15 similar lines)", "[INFO] done"]
    assert result.similar_collapsed == 14
    assert result.duplicates_collapsed == 0


def test_error_lines_are_never_folded_as_similar() -> None:
    raw = (
        "Tests run: 12, Failures: 0, Errors: 0, Skipped: 0\n"
        "Tests run: 12, Failures: 1, Errors: 0, Skipped: 0 <<< FAILURE!\n"
        "Tests run: 12, Failures: 2, Errors: 0, Skipped: 0 <<< FAILURE!\n"
    )

    result = LogProcessor().normalize(raw)

    assert len(result.lines) == 3  # the two failing lines have the same shape but stay apart
    assert result.similar_collapsed == 0


def test_identical_and_similar_runs_are_counted_separately() -> None:
    raw = "Waiting...\nWaiting...\nstep 1\nstep 2\nstep 3\n"

    result = LogProcessor().normalize(raw)

    assert result.lines == ["Waiting...  (repeated 2 times)", "step 1  (3 similar lines)"]
    assert result.duplicates_collapsed == 1
    assert result.similar_collapsed == 2


def test_lines_with_different_words_are_not_similar() -> None:
    raw = "Downloaded a.pom (1 kB)\nDownloaded b.pom (2 kB)\n"

    result = LogProcessor(noise_patterns=()).normalize(raw)

    assert len(result.lines) == 2


# ---------------------------------------------------------------------------
# time gaps
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected_iso"),
    [
        ("2026-09-15T09:06:10.532Z x", "2026-09-15T09:06:10.532000"),
        ("[2026-09-15T09:06:10Z] x", "2026-09-15T09:06:10"),
        ("2026-09-15 09:06:10,532 x", "2026-09-15T09:06:10.532000"),
        ("2026-09-15T09:06:10+03:00 x", "2026-09-15T09:06:10"),
    ],
)
def test_parse_timestamp_formats(line: str, expected_iso: str) -> None:
    parsed = parse_timestamp(line)

    assert parsed is not None
    assert parsed.isoformat() == expected_iso


def test_parse_timestamp_absent() -> None:
    assert parse_timestamp("[INFO] no stamp") is None


def test_large_time_gap_becomes_a_marker_line() -> None:
    raw = (
        "[2026-09-15T09:00:00Z] + terraform apply\n"
        "[2026-09-15T09:10:00Z] Error: timeout while waiting for state to become 'available'\n"
    )

    result = LogProcessor().normalize(raw)

    assert result.lines == [
        "+ terraform apply",
        GAP_MARKER.format(seconds=600),
        "Error: timeout while waiting for state to become 'available'",
    ]
    assert result.gaps_marked == 1


def test_small_gaps_are_ignored() -> None:
    raw = "[2026-09-15T09:00:00Z] a\n[2026-09-15T09:00:30Z] b\n"

    result = LogProcessor().normalize(raw)

    assert result.lines == ["a", "b"]
    assert result.gaps_marked == 0


def test_gap_threshold_is_configurable() -> None:
    raw = "[2026-09-15T09:00:00Z] a\n[2026-09-15T09:00:30Z] b\n"

    result = LogProcessor(gap_threshold_seconds=10).normalize(raw)

    assert GAP_MARKER.format(seconds=30) in result.lines


def test_gap_is_kept_even_when_the_next_line_is_noise() -> None:
    raw = (
        "[2026-09-15T09:00:00Z] a\n"
        "[2026-09-15T09:10:00Z] [Pipeline] { (Deploy)\n"
        "[2026-09-15T09:10:01Z] b\n"
    )

    result = LogProcessor().normalize(raw)

    assert result.lines == ["a", GAP_MARKER.format(seconds=600), "b"]


def test_gap_marker_is_neither_noise_nor_error_nor_folded() -> None:
    marker = GAP_MARKER.format(seconds=600)

    assert is_gap_marker(marker)
    assert not is_noise(marker)
    lines = [marker, GAP_MARKER.format(seconds=900)]
    from app.services.log_processor import _collapse_consecutive_duplicates

    folded, _, similar = _collapse_consecutive_duplicates(lines)
    assert folded == lines
    assert similar == 0


def test_non_consecutive_repeats_are_kept() -> None:
    raw = "A\nB\nA\n"

    result = LogProcessor().normalize(raw)

    assert result.lines == ["A", "B", "A"]
    assert result.duplicates_collapsed == 0


def test_empty_input() -> None:
    result = LogProcessor().normalize("")

    assert result.lines == []
    assert result.total_lines == 0


def test_custom_noise_patterns_can_be_injected() -> None:
    processor = LogProcessor(noise_patterns=())

    result = processor.normalize("[Pipeline] { (Build)\n\n")

    assert result.lines == ["[Pipeline] { (Build)", ""]
    assert result.noise_removed == 0


# ---------------------------------------------------------------------------
# Stage 2: error detection
# ---------------------------------------------------------------------------

from app.services.log_processor import (  # noqa: E402
    SKIP_MARKER,
    ExtractionConfig,
    estimate_tokens,
    extract,
    is_error_line,
    is_warning_line,
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [("", 0), ("abcd", 1), ("abcde", 2), ("x" * 400, 100)],
)
def test_estimate_tokens(text: str, expected: int) -> None:
    assert estimate_tokens(text) == expected


def test_token_budget_limits_the_excerpt() -> None:
    lines = ["x" * 40] * 100  # 100 lines of 40 chars = ~10 tokens each
    lines[50] = "[ERROR] boom" + "!" * 28
    config = ExtractionConfig(context_before=30, context_after=30, tail_lines=0, max_tokens=100)

    excerpt, truncated = extract(lines, config)

    assert truncated
    assert estimate_tokens(excerpt) <= 100 + 10  # skip markers add a little
    assert "[ERROR] boom" in excerpt


@pytest.mark.parametrize(
    "line",
    [
        "[ERROR] Failed to execute goal on project payment-service",
        "[ERROR] BUILD FAILURE",
        "FATAL: command execution failed",
        'java.lang.NullPointerException: Cannot invoke "String.length()"',
        "ValueError: invalid literal for int()",
        "Traceback (most recent call last):",
        "script returned exit code 1",
        "Process exited with exit status 137",
        "  Warning  BackOff  pod/api-7d9f  Back-off restarting failed container",
        "  Normal   Pulling  ... ErrImagePull",
        "Status: ImagePullBackOff",
        "Last State: Terminated  Reason: OOMKilled",
        "Return code is: 401, ReasonPhrase: Unauthorized.",
        "Error response from daemon: pull access denied for registry.example.com/app",
        "bash: kubectl: command not found",
        "cp: cannot stat 'dist/app.jar': No such file or directory",
        "Could not resolve dependencies for project com.example:payment-service",
        "dial tcp 10.0.0.5:5432: connect: connection refused",
        "Tests run: 12, Failures: 1, Errors: 0, Skipped: 0 <<< FAILURE!",
        'error: deployment "api" exceeded its progress deadline',  # kubectl, lowercase
        "npm ERR! code E401",
        "Execution failed for task ':test'.",  # gradle, lowercase
        "> There were failing tests. See the report at: file:///x/index.html",
        "48 tests completed, 1 failed",
        "fatal: not a git repository (or any of the parent directories): .git",
        "error: failed to solve: process did not complete successfully",  # docker buildx
    ],
)
def test_error_lines_are_detected(line: str) -> None:
    assert is_error_line(line)


@pytest.mark.parametrize(
    "line",
    [
        "[INFO] Building payment-service 1.4.2",
        "[INFO] --- maven-compiler-plugin:3.11.0:compile (default-compile) @ payment-service ---",
        "Step 5/12 : RUN mvn -B package",
        "Successfully tagged registry.example.com/app:1.4.2",
        "deployment.apps/api configured",
        "Tests run: 12, Failures: 0, Errors: 0, Skipped: 0",
        "[WARNING] Using platform encoding (UTF-8) to copy filtered resources",
    ],
)
def test_ordinary_lines_are_not_errors(line: str) -> None:
    assert not is_error_line(line)


def test_warning_detection() -> None:
    assert is_warning_line("[WARNING] Using platform encoding")
    assert is_warning_line("WARN  o.s.b.StartupInfoLogger - slow startup")
    assert not is_warning_line("[INFO] all good")


# ---------------------------------------------------------------------------
# Stage 2: extraction
# ---------------------------------------------------------------------------


def _numbered(n: int, prefix: str = "line") -> list[str]:
    return [f"{prefix} {i}" for i in range(n)]


def test_error_gets_context_before_and_after() -> None:
    lines = _numbered(100)
    lines[50] = "[ERROR] boom"
    config = ExtractionConfig(context_before=3, context_after=1, tail_lines=2)

    excerpt, truncated = extract(lines, config)

    assert not truncated
    assert excerpt.splitlines() == [
        SKIP_MARKER.format(n=47),
        "line 47",
        "line 48",
        "line 49",
        "[ERROR] boom",
        "line 51",
        SKIP_MARKER.format(n=46),
        "line 98",
        "line 99",
    ]


def test_stack_trace_after_error_is_kept_whole() -> None:
    lines = [
        "[INFO] running tests",
        "java.lang.IllegalStateException: pool exhausted",
        "\tat com.example.Db.acquire(Db.java:42)",
        "\tat com.example.Service.run(Service.java:17)",
        "Caused by: java.net.ConnectException: Connection refused",
        "\tat java.base/sun.nio.ch.Net.connect(Net.java:579)",
        "\t... 12 more",
        "[INFO] shutting down",
        "[INFO] done",
    ]
    config = ExtractionConfig(context_before=0, context_after=0, tail_lines=1)

    excerpt, _ = extract(lines, config)

    assert "\t... 12 more" in excerpt
    assert "[INFO] shutting down" not in excerpt  # after-window is 0, trace ended
    assert excerpt.splitlines()[-1] == "[INFO] done"  # tail


def test_tail_is_always_included_even_without_errors() -> None:
    lines = _numbered(50)
    config = ExtractionConfig(tail_lines=5)

    excerpt, truncated = extract(lines, config)

    assert excerpt.splitlines() == [SKIP_MARKER.format(n=45), *lines[45:]]
    assert not truncated


def test_overlapping_windows_are_merged_without_duplicate_lines() -> None:
    lines = _numbered(30)
    lines[10] = "[ERROR] first"
    lines[12] = "[ERROR] second"
    config = ExtractionConfig(context_before=2, context_after=2, tail_lines=1)

    excerpt, _ = extract(lines, config)

    body = excerpt.splitlines()
    assert body.count("line 11") == 1
    assert body[1:8] == [
        "line 8",
        "line 9",
        "[ERROR] first",
        "line 11",
        "[ERROR] second",
        "line 13",
        "line 14",
    ]


def test_budget_keeps_tail_and_first_error_and_bare_lines_of_later_errors() -> None:
    lines = _numbered(200)
    lines[20] = "[ERROR] first (root cause?)"
    lines[100] = "[ERROR] second"
    lines[150] = "[ERROR] OutOfMemoryError: Java heap space"
    config = ExtractionConfig(context_before=2, context_after=2, tail_lines=5, max_lines=12)

    excerpt, truncated = extract(lines, config)

    body = excerpt.splitlines()
    assert truncated
    assert "[ERROR] first (root cause?)" in body
    assert "line 199" in body  # tail
    # later errors survive as bare lines: present, but without their context
    assert "[ERROR] second" in body
    assert "[ERROR] OutOfMemoryError: Java heap space" in body
    assert "line 99" not in body
    assert "line 149" not in body
    assert len([line for line in body if not line.startswith("...")]) <= 12


def test_later_errors_are_dropped_only_when_not_even_one_line_fits() -> None:
    lines = _numbered(200)
    lines[20] = "[ERROR] first"
    lines[100] = "[ERROR] second"
    config = ExtractionConfig(context_before=2, context_after=2, tail_lines=5, max_lines=10)

    excerpt, truncated = extract(lines, config)

    assert truncated
    assert "[ERROR] first" in excerpt
    assert "[ERROR] second" not in excerpt  # 5 tail + 5 first-error window = 10, nothing left


def test_first_error_segment_is_shrunk_when_it_alone_exceeds_budget() -> None:
    lines = _numbered(100)
    lines[50] = "[ERROR] boom"
    config = ExtractionConfig(context_before=20, context_after=10, tail_lines=0, max_lines=7)

    excerpt, truncated = extract(lines, config)

    body = [line for line in excerpt.splitlines() if not line.startswith("...")]
    assert truncated
    assert "[ERROR] boom" in body
    assert len(body) <= 7
    # more context above than below, mirroring context_before > context_after
    above = body.index("[ERROR] boom")
    below = len(body) - above - 1
    assert above > below


def test_warnings_are_kept_without_context() -> None:
    lines = _numbered(40)
    lines[10] = "[WARNING] deprecated flag"
    config = ExtractionConfig(context_before=5, context_after=5, tail_lines=1)

    excerpt, _ = extract(lines, config)

    body = excerpt.splitlines()
    assert "[WARNING] deprecated flag" in body
    assert "line 9" not in body
    assert "line 11" not in body


def test_extract_empty_input() -> None:
    assert extract([]) == ("", False)


# ---------------------------------------------------------------------------
# process(): both stages together
# ---------------------------------------------------------------------------


def test_process_end_to_end() -> None:
    raw = "\n".join(
        [
            "[2026-09-15T09:00:00.000Z] [Pipeline] { (Build)",
            "[2026-09-15T09:00:01.000Z] [INFO] Building payment-service 1.4.2",
            *[
                f"[2026-09-15T09:00:02.000Z] [INFO] Downloading from central: https://x/{i}.pom"
                for i in range(500)
            ],
            "[2026-09-15T09:00:03.000Z] [WARNING] Using platform encoding",
            "[2026-09-15T09:00:04.000Z] [ERROR] Failed to execute goal on project payment-service",
            "[2026-09-15T09:00:04.000Z] [ERROR] Return code is: 401, ReasonPhrase: Unauthorized.",
            "[2026-09-15T09:00:05.000Z] [INFO] BUILD FAILURE",
        ]
    )

    result = LogProcessor().process(raw)

    assert result.total_lines == 506
    assert result.normalized_lines == 5
    assert result.first_error == "[ERROR] Failed to execute goal on project payment-service"
    assert result.estimated_tokens == estimate_tokens(result.excerpt) > 0
    assert len(result.error_lines) == 3  # two [ERROR] lines + BUILD FAILURE
    assert result.warning_lines == ["[WARNING] Using platform encoding"]
    assert "Downloading" not in result.excerpt
    assert "[INFO] Building payment-service 1.4.2" in result.excerpt
    assert not result.truncated
