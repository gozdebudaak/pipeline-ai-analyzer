import pytest

from app.services.failure_classifier import (
    DEFAULT_RULES,
    FailureCategory,
    FailureClassifier,
)


@pytest.fixture
def classifier() -> FailureClassifier:
    return FailureClassifier()


@pytest.mark.parametrize(
    ("line", "category", "rule"),
    [
        (
            "Warning  Failed  pod/api-7d9f  Error: ImagePullBackOff",
            FailureCategory.KUBERNETES_DEPLOYMENT,
            "k8s_image_pull",
        ),
        (
            "Back-off restarting failed container: CrashLoopBackOff",
            FailureCategory.KUBERNETES_DEPLOYMENT,
            "k8s_crash_loop",
        ),
        (
            "Warning  Unhealthy  Readiness probe failed: HTTP probe failed with statuscode: 503",
            FailureCategory.KUBERNETES_DEPLOYMENT,
            "k8s_probe_failed",
        ),
        (
            'error: deployment "api" exceeded its progress deadline',
            FailureCategory.KUBERNETES_DEPLOYMENT,
            "k8s_rollout_failed",
        ),
        (
            "Last State: Terminated  Reason: OOMKilled  Exit Code: 137",
            FailureCategory.RESOURCE_LIMIT,
            "k8s_oom_killed",
        ),
        (
            "Warning  FailedScheduling  0/3 nodes are available: 3 Insufficient memory.",
            FailureCategory.RESOURCE_LIMIT,
            "k8s_failed_scheduling",
        ),
        (
            'ERROR: failed to solve: process "/bin/sh -c mvn package" did not complete successfully: exit code: 1',
            FailureCategory.CONTAINER_BUILD,
            "docker_build_failed",
        ),
        (
            "Error response from daemon: pull access denied for registry.example.com/app, repository does not exist or may require 'docker login'",
            FailureCategory.CONTAINER_REGISTRY,
            "docker_registry_denied",
        ),
        (
            "Error response from daemon: manifest for registry.example.com/app:1.4.3 not found: manifest unknown",
            FailureCategory.CONTAINER_REGISTRY,
            "docker_manifest_missing",
        ),
        (
            "[ERROR] Failed to execute goal on project payment-service: Could not resolve dependencies for project com.example:payment-service:jar:1.4.2",
            FailureCategory.DEPENDENCY_RESOLUTION,
            "maven_dependency",
        ),
        ("[ERROR] COMPILATION ERROR :", FailureCategory.COMPILATION, "maven_compilation"),
        (
            "[ERROR] /src/main/java/App.java:[12,8] cannot find symbol",
            FailureCategory.COMPILATION,
            "maven_compilation",
        ),
        ("[ERROR] There are test failures.", FailureCategory.TEST_FAILURE, "maven_tests"),
        (
            "Tests run: 12, Failures: 1, Errors: 0, Skipped: 0 <<< FAILURE!",
            FailureCategory.TEST_FAILURE,
            "maven_tests",
        ),
        (
            "[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.",
            FailureCategory.AUTHENTICATION,
            "http_401",
        ),
        (
            "HTTP 403 Forbidden while fetching https://repo.example.com/libs-release/",
            FailureCategory.AUTHORIZATION,
            "http_403",
        ),
        ("bash: ./deploy.sh: Permission denied", FailureCategory.AUTHORIZATION, "access_denied"),
        ("dial tcp 10.0.0.5:5432: connect: connection refused", FailureCategory.NETWORK, "network"),
        (
            "[ERROR] Received fatal alert: handshake_failure",
            FailureCategory.NETWORK,
            "tls_handshake",
        ),
        (
            "PKIX path building failed: unable to find valid certification path to requested target",
            FailureCategory.NETWORK,
            "tls_handshake",
        ),
        ("x509: certificate signed by unknown authority", FailureCategory.NETWORK, "tls_handshake"),
        (
            "bash: kubectl: command not found",
            FailureCategory.CONFIGURATION,
            "missing_file_or_command",
        ),
        (
            "./deploy.sh: line 12: DEPLOY_ENV: unbound variable",
            FailureCategory.CONFIGURATION,
            "missing_env_var",
        ),
        (
            "> Could not resolve all files for configuration ':compileClasspath'.",
            FailureCategory.DEPENDENCY_RESOLUTION,
            "gradle_dependency",
        ),
        (
            "Execution failed for task ':compileJava'.",
            FailureCategory.COMPILATION,
            "gradle_compilation",
        ),
        (
            "> There were failing tests. See the report at: file:///workspace/build/reports/tests/test/index.html",
            FailureCategory.TEST_FAILURE,
            "gradle_tests",
        ),
        ("> Task :test FAILED", FailureCategory.TEST_FAILURE, "gradle_tests"),
        ("npm ERR! code E401", FailureCategory.AUTHENTICATION, "npm_auth"),
        ("npm ERR! code E403", FailureCategory.AUTHORIZATION, "npm_forbidden"),
        ("npm ERR! code ERESOLVE", FailureCategory.DEPENDENCY_RESOLUTION, "npm_dependency"),
        ("npm ERR! code ENOTFOUND", FailureCategory.NETWORK, "npm_network"),
        (
            "ERROR: No matching distribution found for example-payments-sdk==3.2.0",
            FailureCategory.DEPENDENCY_RESOLUTION,
            "pip_dependency",
        ),
        (
            "Error: UPGRADE FAILED: timed out waiting for the condition",
            FailureCategory.KUBERNETES_DEPLOYMENT,
            "helm_release_failed",
        ),
        (
            "Error: values don't meet the specifications of the schema(s) in the following chart(s):",
            FailureCategory.CONFIGURATION,
            "helm_values_invalid",
        ),
        (
            "OSError: [Errno 28] No space left on device",
            FailureCategory.RESOURCE_LIMIT,
            "disk_full",
        ),
        (
            "write /var/lib/docker/tmp/GetImageBlob123: no space left on device",
            FailureCategory.RESOURCE_LIMIT,
            "disk_full",
        ),
    ],
)
def test_single_line_classification(
    classifier: FailureClassifier, line: str, category: FailureCategory, rule: str
) -> None:
    result = classifier.classify([line])

    assert result.category is category
    assert rule in result.matched_rules
    assert result.is_known


def test_no_match_is_unknown(classifier: FailureClassifier) -> None:
    result = classifier.classify(["[INFO] BUILD SUCCESS", "something odd happened"])

    assert result.category is FailureCategory.UNKNOWN
    assert result.matched_rules == []
    assert not result.is_known


def test_empty_input_is_unknown(classifier: FailureClassifier) -> None:
    assert classifier.classify([]).category is FailureCategory.UNKNOWN


def test_definite_signal_beats_symptom_even_when_symptom_repeats(
    classifier: FailureClassifier,
) -> None:
    """The cause/effect case from the spec: 401 is the cause, unresolved deps the symptom."""
    lines = [
        "[ERROR] Could not resolve dependencies for project com.example:payment-service",
        "[ERROR] Could not transfer artifact com.example:lib:jar:1.0 from/to artifactory",
        "[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.",
    ]

    result = classifier.classify(lines)

    assert result.category is FailureCategory.AUTHENTICATION
    assert result.matched_rules == ["maven_dependency", "artifactory", "http_401"]
    assert result.scores == {
        FailureCategory.DEPENDENCY_RESOLUTION: 5,
        FailureCategory.ARTIFACT_REPOSITORY: 2,
        FailureCategory.AUTHENTICATION: 10,
    }


def test_a_rule_counts_once_however_many_lines_it_matches(classifier: FailureClassifier) -> None:
    symptom = "[ERROR] Could not resolve dependencies for project x"
    lines = [symptom] * 50 + ["[ERROR] Return code is: 401, ReasonPhrase: Unauthorized."]

    result = classifier.classify(lines)

    assert result.category is FailureCategory.AUTHENTICATION
    assert result.scores[FailureCategory.DEPENDENCY_RESOLUTION] == 5


def test_two_definite_signals_of_one_category_add_up(classifier: FailureClassifier) -> None:
    lines = [
        "Warning  Failed  pod/api-7d9f  Error: ImagePullBackOff",
        "Back-off restarting failed container: CrashLoopBackOff",
        "[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.",
    ]

    result = classifier.classify(lines)

    assert result.category is FailureCategory.KUBERNETES_DEPLOYMENT
    assert result.scores[FailureCategory.KUBERNETES_DEPLOYMENT] == 18


def test_registry_denied_beats_generic_access_denied(classifier: FailureClassifier) -> None:
    """'pull access denied' matches both rules; the registry-specific one must win."""
    line = "Error response from daemon: pull access denied for registry.example.com/app"

    result = classifier.classify([line])

    assert result.category is FailureCategory.CONTAINER_REGISTRY
    assert set(result.matched_rules) == {"docker_registry_denied", "access_denied"}


@pytest.mark.parametrize(
    ("line", "severity"),
    [
        ("Last State: Terminated  Reason: OOMKilled  Exit Code: 137", "critical"),
        ("Back-off restarting failed container: CrashLoopBackOff", "critical"),
        ("Warning  Failed  pod/api-7d9f  Error: ImagePullBackOff", "high"),
        ("[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.", "high"),
        ("[ERROR] COMPILATION ERROR :", "medium"),
        ("[ERROR] There are test failures.", "medium"),
        ("OSError: [Errno 28] No space left on device", "high"),
    ],
)
def test_rule_based_severity(classifier: FailureClassifier, line: str, severity: str) -> None:
    assert classifier.classify([line]).severity == severity


def test_worst_matched_severity_wins(classifier: FailureClassifier) -> None:
    lines = [
        "[ERROR] COMPILATION ERROR :",  # medium
        "Last State: Terminated  Reason: OOMKilled",  # critical
    ]

    assert classifier.classify(lines).severity == "critical"


def test_location_hint_alone_gives_no_severity(classifier: FailureClassifier) -> None:
    result = classifier.classify(["from/to artifactory"])

    assert result.category is FailureCategory.ARTIFACT_REPOSITORY
    assert result.severity is None


def test_unknown_has_no_severity(classifier: FailureClassifier) -> None:
    assert classifier.classify(["nothing here"]).severity is None


def test_weights_stay_within_the_documented_bands() -> None:
    for rule in DEFAULT_RULES:
        assert 1 <= rule.weight <= 10, rule.name


def test_rule_names_are_unique() -> None:
    names = [rule.name for rule in DEFAULT_RULES]

    assert len(names) == len(set(names))


def test_custom_rules_can_be_injected() -> None:
    classifier = FailureClassifier(rules=())

    assert classifier.classify(["ImagePullBackOff"]).category is FailureCategory.UNKNOWN
