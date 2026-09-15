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
        ("dial tcp 10.0.0.5:5432: connect: connection refused", FailureCategory.NETWORK, "network"),
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


def test_category_with_most_matching_lines_wins(classifier: FailureClassifier) -> None:
    lines = [
        "Warning  Failed  pod/api-7d9f  Error: ImagePullBackOff",
        "Warning  Failed  pod/api-7d9f  Error: ImagePullBackOff",
        "dial tcp 10.0.0.5:5432: connect: connection refused",
    ]

    result = classifier.classify(lines)

    assert result.category is FailureCategory.KUBERNETES_DEPLOYMENT
    assert result.matched_rules == ["k8s_image_pull", "network"]


def test_rule_names_are_unique() -> None:
    names = [rule.name for rule in DEFAULT_RULES]

    assert len(names) == len(set(names))


def test_custom_rules_can_be_injected() -> None:
    classifier = FailureClassifier(rules=())

    assert classifier.classify(["ImagePullBackOff"]).category is FailureCategory.UNKNOWN
