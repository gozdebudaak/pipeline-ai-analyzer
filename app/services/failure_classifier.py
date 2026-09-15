"""Deterministic failure classification.

Before the LLM sees anything, known failure signatures are matched against
the error lines with plain regular expressions. The result is used as a
hint in the prompt and as a cross-check on the LLM's own answer.

Mechanism (kept deliberately simple): every rule is (name, category, regex).
Each rule is tried on each error line; the category with the most matching
lines wins. No match at all means ``UNKNOWN``.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum


class FailureCategory(StrEnum):
    CONTAINER_BUILD = "container_build"
    CONTAINER_REGISTRY = "container_registry"
    KUBERNETES_DEPLOYMENT = "kubernetes_deployment"
    DEPENDENCY_RESOLUTION = "dependency_resolution"
    AUTHENTICATION = "authentication"
    AUTHORIZATION = "authorization"
    COMPILATION = "compilation"
    TEST_FAILURE = "test_failure"
    NETWORK = "network"
    CONFIGURATION = "configuration"
    RESOURCE_LIMIT = "resource_limit"
    ARTIFACT_REPOSITORY = "artifact_repository"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassificationRule:
    name: str
    category: FailureCategory
    pattern: re.Pattern[str]


def _rule(name: str, category: FailureCategory, regex: str) -> ClassificationRule:
    return ClassificationRule(name, category, re.compile(regex, re.IGNORECASE))


# One rule per well-known failure signature. Names are stable identifiers:
# they appear in results, logs and (later) metrics.
DEFAULT_RULES: tuple[ClassificationRule, ...] = (
    # Kubernetes
    _rule(
        "k8s_image_pull", FailureCategory.KUBERNETES_DEPLOYMENT, r"ImagePullBackOff|ErrImagePull"
    ),
    _rule("k8s_crash_loop", FailureCategory.KUBERNETES_DEPLOYMENT, r"CrashLoopBackOff"),
    _rule(
        "k8s_probe_failed",
        FailureCategory.KUBERNETES_DEPLOYMENT,
        r"(?:Liveness|Readiness|Startup) probe failed",
    ),
    _rule(
        "k8s_rollout_failed",
        FailureCategory.KUBERNETES_DEPLOYMENT,
        r"exceeded its progress deadline|rollout (?:failed|timed out)",
    ),
    _rule("k8s_oom_killed", FailureCategory.RESOURCE_LIMIT, r"OOMKilled"),
    _rule(
        "k8s_failed_scheduling",
        FailureCategory.RESOURCE_LIMIT,
        r"FailedScheduling|Insufficient (?:cpu|memory)",
    ),
    # Docker
    _rule(
        "docker_build_failed",
        FailureCategory.CONTAINER_BUILD,
        r"failed to solve|did not complete successfully|returned a non-zero code|dockerfile parse error",
    ),
    _rule(
        "docker_registry_denied",
        FailureCategory.CONTAINER_REGISTRY,
        r"pull access denied|denied: requested access to the resource is denied|unauthorized: authentication required",
    ),
    _rule(
        "docker_manifest_missing",
        FailureCategory.CONTAINER_REGISTRY,
        r"manifest (?:unknown|not found)|manifest for .+ not found",
    ),
    # Maven / Java
    _rule(
        "maven_dependency",
        FailureCategory.DEPENDENCY_RESOLUTION,
        r"Could not resolve dependencies|Could not find artifact|Could not transfer artifact",
    ),
    _rule(
        "maven_compilation",
        FailureCategory.COMPILATION,
        r"COMPILATION ERROR|cannot find symbol|package .+ does not exist|incompatible types",
    ),
    _rule(
        "maven_tests",
        FailureCategory.TEST_FAILURE,
        r"There are test failures|Tests run: \d+, Failures: [1-9]|Tests run: \d+, Failures: \d+, Errors: [1-9]",
    ),
    # Artifactory
    _rule("artifactory", FailureCategory.ARTIFACT_REPOSITORY, r"artifactory"),
    # Cross-cutting
    _rule(
        "http_401",
        FailureCategory.AUTHENTICATION,
        r"\b401\b|Unauthorized|authentication (?:failed|required)|invalid credentials",
    ),
    _rule(
        "http_403",
        FailureCategory.AUTHORIZATION,
        r"\b403\b|Forbidden|permission denied|access denied",
    ),
    _rule(
        "network",
        FailureCategory.NETWORK,
        r"connection refused|timed out|Could not resolve host|UnknownHostException|no route to host|Network is unreachable",
    ),
    _rule(
        "missing_file_or_command",
        FailureCategory.CONFIGURATION,
        r"No such file or directory|command not found",
    ),
    _rule(
        "missing_env_var",
        FailureCategory.CONFIGURATION,
        r"environment variable .+ (?:is )?not set|unbound variable|Missing required (?:environment|config)",
    ),
)


@dataclass(frozen=True)
class Classification:
    category: FailureCategory
    matched_rules: list[str] = field(default_factory=list)  # rule names, in match order

    @property
    def is_known(self) -> bool:
        return self.category is not FailureCategory.UNKNOWN


class FailureClassifier:
    def __init__(self, rules: tuple[ClassificationRule, ...] = DEFAULT_RULES) -> None:
        self._rules = rules

    def classify(self, lines: list[str]) -> Classification:
        """Pick the category whose rules matched the most lines."""
        votes: Counter[FailureCategory] = Counter()
        matched: list[str] = []

        for line in lines:
            for rule in self._rules:
                if rule.pattern.search(line):
                    votes[rule.category] += 1
                    if rule.name not in matched:
                        matched.append(rule.name)

        if not votes:
            return Classification(FailureCategory.UNKNOWN)

        # most_common is stable: on a tie the category seen first wins.
        category, _ = votes.most_common(1)[0]
        return Classification(category, matched)
