import pytest

from app.services.log_processor import (
    LogProcessor,
    is_noise,
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
