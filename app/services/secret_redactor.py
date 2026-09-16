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
from collections.abc import Sequence
from dataclasses import dataclass, field

REDACTED = "[REDACTED]"


@dataclass(frozen=True)
class SecretPattern:
    """A named regular expression and the replacement applied to its matches."""

    name: str
    regex: re.Pattern[str]
    replacement: str


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
)


class SecretRedactor:
    """Replace secrets in free text with ``[REDACTED]`` markers."""

    def __init__(self, patterns: Sequence[SecretPattern] = DEFAULT_PATTERNS) -> None:
        self._patterns = tuple(patterns)

    def redact(self, text: str) -> RedactionResult:
        counts: dict[str, int] = {}
        for pattern in self._patterns:
            text, n = pattern.regex.subn(pattern.replacement, text)
            if n:
                counts[pattern.name] = n
        return RedactionResult(text=text, counts=counts)
