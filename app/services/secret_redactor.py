"""Secret redaction.

Nothing derived from a pipeline log may reach an external LLM before it has
passed through :class:`SecretRedactor`. Detection is deterministic (regular
expressions), so it is fast, offline and unit-testable.

Design rules:

* Keep the *name* of a secret (``PASSWORD=[REDACTED]``) so the analysis can
  still reason about "a credential was involved"; drop only the value.
* Prefer false positives over false negatives: redacting a harmless value
  slightly weakens the analysis, missing a real secret is a security incident.
* Patterns are applied in order, from large blocks to specific tokens to the
  generic ``KEY=value`` rule, so one secret is counted once.
"""

import re
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from math import log2

REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class SecretPattern:
    """A named regular expression and the replacement applied to its matches."""

    name: str
    regex: re.Pattern[str]
    # A string is substituted as is. A callable gets each match and returns the text to put
    # in its place; returning the match unchanged means "not a secret after all".
    replacement: str | Callable[[re.Match[str]], str]


@dataclass(frozen=True)
class RedactionResult:
    text: str
    counts: dict[str, int] = field(default_factory=dict)  # pattern name -> matches

    @property
    def total(self) -> int:
        return sum(self.counts.values())


_KEY_NAME_ALTERNATION = (
    r"password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|auth[_-]?token|"
    r"client[_-]?secret|private[_-]?key|credentials?|secret[_-]?access[_-]?key|"
    r"session[_-]?key|encryption[_-]?key|signing[_-]?key|"
    r"certificate[_-]?data|key[_-]?data|certificate[_-]?authority[_-]?data|"
    r"private[_-]?key[_-]?id"
)


def shannon_entropy(text: str) -> float:
    """Bits of information per character: 0.0 for "aaaa", up to 6.0 for random base64."""
    n = len(text)
    return -sum(c / n * log2(c / n) for c in Counter(text).values())


ENTROPY_THRESHOLD = 4.0  # measured: AWS secret 4.66, git SHA 3.94, CamelCase identifier 3.90
_CHECKSUM_PREFIXES = ("sha256-", "sha384-", "sha512-")  # npm/yarn integrity hashes


def _redact_if_random(match: re.Match[str]) -> str:
    """The safety net: no name, no prefix, no known shape, but it looks random."""
    token = match.group(0)
    if token.startswith(_CHECKSUM_PREFIXES):
        return token
    lower = sum(c.islower() for c in token)
    upper = sum(c.isupper() for c in token)
    digits = sum(c.isdigit() for c in token)
    if min(lower, upper, digits) < 2:  # hex hashes, UUIDs, pod names, timestamps, plain words
        return token
    return REDACTED if shannon_entropy(token) >= ENTROPY_THRESHOLD else token


DEFAULT_PATTERNS: tuple[SecretPattern, ...] = (
    # -----BEGIN RSA PRIVATE KEY----- ... -----END RSA PRIVATE KEY-----
    SecretPattern(
        name="private_key_block",
        regex=re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.DOTALL,
        ),
        replacement="[REDACTED PRIVATE KEY]",
    ),
    # https://user:s3cret@host/path  ->  https://[REDACTED]:[REDACTED]@host/path
    SecretPattern(
        name="url_credentials",
        regex=re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s:@]+:[^@\s/]+@"),
        replacement=rf"\g<scheme>{REDACTED}:{REDACTED}@",
    ),
    # Authorization: Bearer <token>   /   Authorization: Basic <base64>
    SecretPattern(
        name="authorization_header",
        regex=re.compile(
            r"(?P<prefix>authorization\s*[:=]\s*(?:bearer|basic|token|digest)\s+)\S+",
            re.IGNORECASE,
        ),
        replacement=rf"\g<prefix>{REDACTED}",
    ),
    # curl / wget / git style user flag:  -u user:pass   --user=user:pass
    SecretPattern(
        name="user_flag_credentials",
        regex=re.compile(r"(?P<flag>(?:^|\s)(?:-u|--user)[\s=]+)[^\s:@]+:\S+"),
        replacement=rf"\g<flag>{REDACTED}:{REDACTED}",
    ),
    # API-key style HTTP headers: X-JFrog-Art-Api: ..., X-Api-Key: ..., PRIVATE-TOKEN: ...
    SecretPattern(
        name="api_key_header",
        regex=re.compile(
            r"(?P<prefix>\b(?:X-JFrog-Art-Api|X-Api-Key|X-Auth-Token|X-Access-Token|"
            r"X-Vault-Token|Private-Token|Api-Key)\s*:\s*)\S+",
            re.IGNORECASE,
        ),
        replacement=rf"\g<prefix>{REDACTED}",
    ),
    # Docker config.json style: "auth": "dXNlcjpwYXNz"
    SecretPattern(
        name="docker_auth_json",
        regex=re.compile(r'(?P<prefix>"auth"\s*:\s*")[^"]+(?P<suffix>")', re.IGNORECASE),
        replacement=rf"\g<prefix>{REDACTED}\g<suffix>",
    ),
    # JSON Web Tokens: three base64url segments, the first always starts with "eyJ".
    SecretPattern(
        name="jwt",
        regex=re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),
        replacement=REDACTED,
    ),
    # Well-known token formats with a fixed prefix.
    SecretPattern(
        name="known_token_prefix",
        regex=re.compile(
            r"\b(?:"
            r"ghp_[A-Za-z0-9]{36}|"  # GitHub personal access token
            r"gho_[A-Za-z0-9]{36}|"  # GitHub OAuth token
            r"ghs_[A-Za-z0-9]{36}|"  # GitHub app installation token
            r"github_pat_[A-Za-z0-9_]{22,}|"  # GitHub fine-grained PAT
            r"glpat-[A-Za-z0-9_-]{20,}|"  # GitLab PAT
            r"xox[abprs]-[A-Za-z0-9-]{10,}|"  # Slack tokens
            r"sk-[A-Za-z0-9_-]{20,}|"  # OpenAI style keys
            r"AKIA[A-Z0-9]{16}|"  # AWS access key id
            r"ASIA[A-Z0-9]{16}|"  # AWS temporary access key id
            r"AIza[A-Za-z0-9_-]{35}"  # Google API key
            r")\b"
        ),
        replacement=REDACTED,
    ),
    SecretPattern(
        name="webhook_url",
        regex=re.compile(
            r"(?P<prefix>https://(?:hooks\.slack\.com/services/|discord(?:app)?\.com/api/webhooks/))"
            r"[^\s\"'<>]+"
        ),
        replacement=rf"\g<prefix>{REDACTED}",
    ),
    SecretPattern(
        name="aws_secret_key",
        regex=re.compile(
            r"(?<![A-Za-z0-9/+=])(?![0-9a-fA-F]{40}(?![A-Za-z0-9/+=]))"
            r"[A-Za-z0-9/+]{40}(?![A-Za-z0-9/+=])"
        ),
        replacement=REDACTED,
    ),
    # Generic  KEY=value / KEY: value / --key value / "key": "value"
    # The key name is kept, only the value is replaced.
    SecretPattern(
        name="key_value",
        regex=re.compile(
            rf"(?P<key>(?:--)?[\"']?(?:[A-Za-z0-9_.-]*(?:{_KEY_NAME_ALTERNATION}))[\"']?)"
            r"(?P<sep>\s*[=:]\s*|\s+)"
            r"(?P<quote>[\"']?)"
            r"(?!\[REDACTED)"  # never re-match a value an earlier pattern already redacted
            r"(?P<value>[^\s\"',;]+)"
            r"(?P=quote)",
            re.IGNORECASE,
        ),
        replacement=rf"\g<key>\g<sep>\g<quote>{REDACTED}\g<quote>",
    ),
    # Last resort: any 20+ character word from the base64/base64url alphabet that is mixed
    # case with digits and has high Shannon entropy. Catches keys we have no pattern for.
    # "/" and "=" are deliberately not word characters: they would glue paths and
    # assignments (ARTIFACT=target/app-1) into one "random-looking" token.
    SecretPattern(
        name="high_entropy",
        regex=re.compile(r"(?<![A-Za-z0-9+_-])[A-Za-z0-9+_-]{20,}={0,2}(?![A-Za-z0-9+_=-])"),
        replacement=_redact_if_random,
    ),
)


class SecretRedactor:
    """Replace secrets in free text with ``[REDACTED]`` markers."""

    def __init__(self, patterns: Sequence[SecretPattern] = DEFAULT_PATTERNS) -> None:
        self._patterns = tuple(patterns)

    def redact(self, text: str) -> RedactionResult:
        counts: dict[str, int] = {}
        for pattern in self._patterns:
            text, n = _substitute(pattern, text)
            if n:
                counts[pattern.name] = n
        return RedactionResult(text=text, counts=counts)


def _substitute(pattern: SecretPattern, text: str) -> tuple[str, int]:
    """Apply one pattern; count only matches that were actually changed."""
    replacement = pattern.replacement
    if isinstance(replacement, str):
        return pattern.regex.subn(replacement, text)

    changed = 0

    def repl(match: re.Match[str]) -> str:
        nonlocal changed
        out = replacement(match)
        changed += out != match.group(0)
        return out

    return pattern.regex.sub(repl, text), changed
