"""Deterministic failure classification.

Before the LLM sees anything, known failure signatures are matched against
the error lines with plain regular expressions. The result is used as a
hint in the prompt and as a cross-check on the LLM's own answer.

Mechanism: every rule is (name, category, regex, weight). Each rule is tried
on each error line. A rule that matches counts **once**, however many lines
it matched: a symptom repeated fifty times is still one symptom. The
category with the highest total weight wins; no match means ``UNKNOWN``.

Weight bands (use them when adding rules):
    8-10  definite signal: names the cause on its own (401, OOMKilled, ImagePullBackOff)
    4-5   symptom: a consequence with many possible causes (Could not resolve dependencies)
    1-2   location hint: says where, not what (the word "artifactory")

Severity bands, by operational impact ("what is broken for whom"):
    critical  a running workload is affected (CrashLoopBackOff, OOMKilled, probe failures)
    high      a deployment or build is blocked by infrastructure, auth or network
    medium    the developer fixes it in the code base (compilation, tests, Dockerfile)
    (none)    the rule alone says nothing about impact (location hints)
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

Severity = Literal["low", "medium", "high", "critical"]
_SEVERITY_ORDER: dict[str, int] = {"low": 0, "medium": 1, "high": 2, "critical": 3}


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
    weight: int  # see the weight bands in the module docstring
    severity: Severity | None = None  # operational impact when this signature is present


def _rule(
    name: str,
    category: FailureCategory,
    regex: str,
    weight: int,
    severity: Severity | None = None,
) -> ClassificationRule:
    return ClassificationRule(name, category, re.compile(regex, re.IGNORECASE), weight, severity)


# One rule per well-known failure signature. Names are stable identifiers:
# they appear in results, logs and (later) metrics.
C = FailureCategory
DEFAULT_RULES: tuple[ClassificationRule, ...] = (
    # --- Kubernetes ---------------------------------------------------------------
    _rule("k8s_image_pull", C.KUBERNETES_DEPLOYMENT, r"ImagePullBackOff|ErrImagePull", 9, "high"),
    _rule("k8s_crash_loop", C.KUBERNETES_DEPLOYMENT, r"CrashLoopBackOff", 9, "critical"),
    _rule(
        "k8s_probe_failed",
        C.KUBERNETES_DEPLOYMENT,
        r"(?:Liveness|Readiness|Startup) probe failed",
        8,
        "critical",
    ),
    _rule(
        "k8s_rollout_failed",
        C.KUBERNETES_DEPLOYMENT,
        r"exceeded its progress deadline|rollout (?:failed|timed out)",
        5,
        "high",
    ),
    _rule("k8s_oom_killed", C.RESOURCE_LIMIT, r"OOMKilled", 10, "critical"),
    _rule(
        "k8s_failed_scheduling",
        C.RESOURCE_LIMIT,
        r"FailedScheduling|Insufficient (?:cpu|memory)",
        9,
        "critical",
    ),
    # --- Docker -------------------------------------------------------------------
    _rule(
        "docker_build_failed",
        C.CONTAINER_BUILD,
        r"failed to solve|did not complete successfully|returned a non-zero code|dockerfile parse error",
        5,
        "medium",
    ),
    _rule(
        "docker_registry_denied",
        C.CONTAINER_REGISTRY,
        r"pull access denied|denied: requested access to the resource is denied|unauthorized: authentication required",
        9,
        "high",
    ),
    _rule(
        "docker_manifest_missing",
        C.CONTAINER_REGISTRY,
        r"manifest (?:unknown|not found)|manifest for .+ not found",
        9,
        "high",
    ),
    # --- Maven / Java -------------------------------------------------------------
    _rule(
        "maven_dependency",
        C.DEPENDENCY_RESOLUTION,
        r"Could not resolve dependencies|Could not find artifact|Could not transfer artifact",
        5,
        "high",
    ),
    _rule(
        "maven_compilation",
        C.COMPILATION,
        r"COMPILATION ERROR|cannot find symbol|package .+ does not exist|incompatible types",
        9,
        "medium",
    ),
    _rule(
        "maven_tests",
        C.TEST_FAILURE,
        r"There are test failures|Tests run: \d+, Failures: [1-9]|Tests run: \d+, Failures: \d+, Errors: [1-9]",
        9,
        "medium",
    ),
    # --- Artifactory --------------------------------------------------------------
    _rule("artifactory", C.ARTIFACT_REPOSITORY, r"artifactory", 2),
    # --- Cross-cutting ------------------------------------------------------------
    _rule(
        "http_401",
        C.AUTHENTICATION,
        r"\b401\b|Unauthorized|authentication (?:failed|required)|invalid credentials",
        10,
        "high",
    ),
    _rule("http_403", C.AUTHORIZATION, r"\b403\b|Forbidden", 10, "high"),
    # Generic phrasing: also printed by docker, kubectl, shells; weaker than a bare 403.
    _rule("access_denied", C.AUTHORIZATION, r"permission denied|access denied", 6, "medium"),
    _rule(
        "network",
        C.NETWORK,
        r"connection refused|timed out|Could not resolve host|UnknownHostException|no route to host|Network is unreachable",
        5,
        "high",
    ),
    # TLS failures as printed by Java/Maven, Python, Go and curl. Usually a missing CA in the truststore.
    _rule(
        "tls_handshake",
        C.NETWORK,
        r"handshake_failure|SSLHandshakeException|PKIX path building failed|certificate verify failed|unable to get local issuer certificate|x509: certificate",
        8,
        "high",
    ),
    _rule(
        "missing_file_or_command",
        C.CONFIGURATION,
        r"No such file or directory|command not found",
        7,
        "medium",
    ),
    _rule(
        "missing_env_var",
        C.CONFIGURATION,
        r"environment variable .+ (?:is )?not set|unbound variable|Missing required (?:environment|config)",
        8,
        "medium",
    ),
)


@dataclass(frozen=True)
class Classification:
    category: FailureCategory
    matched_rules: list[str] = field(default_factory=list)  # rule names, in table order
    scores: dict[FailureCategory, int] = field(default_factory=dict)  # why the winner won
    severity: Severity | None = None  # highest severity among matched rules

    @property
    def is_known(self) -> bool:
        return self.category is not FailureCategory.UNKNOWN


class FailureClassifier:
    def __init__(self, rules: tuple[ClassificationRule, ...] = DEFAULT_RULES) -> None:
        self._rules = rules

    def classify(self, lines: list[str]) -> Classification:
        """Pick the category with the highest total weight of distinct matched rules."""
        matched: list[str] = []
        scores: Counter[FailureCategory] = Counter()
        severity: Severity | None = None

        for rule in self._rules:
            if any(rule.pattern.search(line) for line in lines):
                matched.append(rule.name)
                scores[rule.category] += rule.weight  # once per rule, not per line
                if rule.severity is not None and (
                    severity is None or _SEVERITY_ORDER[rule.severity] > _SEVERITY_ORDER[severity]
                ):
                    severity = rule.severity  # the worst matched signature sets the severity

        if not scores:
            return Classification(FailureCategory.UNKNOWN)

        # most_common is stable: on a tie, the category whose rule comes first wins.
        category, _ = scores.most_common(1)[0]
        return Classification(category, matched, dict(scores), severity)
