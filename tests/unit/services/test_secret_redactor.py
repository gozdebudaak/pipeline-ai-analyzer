import pytest

from app.services.secret_redactor import REDACTED, SecretRedactor


@pytest.fixture
def redactor() -> SecretRedactor:
    return SecretRedactor()


# ---------------------------------------------------------------------------
# The examples from the project specification must work exactly as described.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("PASSWORD=mySecret", "PASSWORD=[REDACTED]"),
        ("TOKEN=abcdef123", "TOKEN=[REDACTED]"),
        ("API_KEY=xyz", "API_KEY=[REDACTED]"),
        (
            "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456",
            "Authorization: Bearer [REDACTED]",
        ),
    ],
)
def test_specification_examples(redactor: SecretRedactor, line: str, expected: str) -> None:
    assert redactor.redact(line).text == expected


# ---------------------------------------------------------------------------
# Generic KEY=value forms
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("DB_PASSWORD=hunter2", "DB_PASSWORD=[REDACTED]"),
        ("db_password: hunter2", "db_password: [REDACTED]"),
        ('"password": "hunter2"', '"password": "[REDACTED]"'),
        ("password='hunter2'", "password='[REDACTED]'"),
        ("--token abc123", "--token [REDACTED]"),
        ("export ARTIFACTORY_API_KEY=AKCp8abc", "export ARTIFACTORY_API_KEY=[REDACTED]"),
        ("AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG", "AWS_SECRET_ACCESS_KEY=[REDACTED]"),
        ("client_secret=abc-def", "client_secret=[REDACTED]"),
    ],
)
def test_key_value_forms(redactor: SecretRedactor, line: str, expected: str) -> None:
    result = redactor.redact(line)

    assert result.text == expected
    assert result.counts == {"key_value": 1}


def test_key_name_is_preserved_only_value_is_removed(redactor: SecretRedactor) -> None:
    result = redactor.redact("PASSWORD=mySecret")

    assert "PASSWORD" in result.text
    assert "mySecret" not in result.text


# ---------------------------------------------------------------------------
# Specific formats
# ---------------------------------------------------------------------------


def test_credentials_embedded_in_url(redactor: SecretRedactor) -> None:
    line = "Downloading from https://deploy:s3cr3t@artifactory.example.com/repo/lib.jar"

    result = redactor.redact(line)

    assert (
        result.text
        == "Downloading from https://[REDACTED]:[REDACTED]@artifactory.example.com/repo/lib.jar"
    )
    assert result.counts == {"url_credentials": 1}


def test_url_without_credentials_is_untouched(redactor: SecretRedactor) -> None:
    line = "GET https://artifactory.example.com:8081/artifactory/api/repos"

    assert redactor.redact(line).text == line


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        (
            "curl -sS -u ci-payment:AKCp8k3Jd9sLx2mQw7vB4nH6tY1uZ0pR -T app.jar https://x/",
            "curl -sS -u [REDACTED]:[REDACTED] -T app.jar https://x/",
        ),
        (
            "wget --user=deploy:s3cret https://x/file",
            "wget --user=[REDACTED]:[REDACTED] https://x/file",
        ),
    ],
)
def test_user_flag_credentials(redactor: SecretRedactor, line: str, expected: str) -> None:
    result = redactor.redact(line)

    assert result.text == expected
    assert result.counts == {"user_flag_credentials": 1}


@pytest.mark.parametrize(
    "line",
    [
        "-H 'X-JFrog-Art-Api: AKCp8k3Jd9sLx2mQw7vB4nH6tY1uZ0pR'",
        "X-Api-Key: 9f8e7d6c5b4a",
        'curl -H "PRIVATE-TOKEN: glpat-abc" https://gitlab/',
    ],
)
def test_api_key_headers(redactor: SecretRedactor, line: str) -> None:
    result = redactor.redact(line)

    assert "[REDACTED]" in result.text
    assert "api_key_header" in result.counts


def test_basic_authorization_header(redactor: SecretRedactor) -> None:
    result = redactor.redact("authorization: Basic dXNlcjpwYXNzd29yZA==")

    assert result.text == "authorization: Basic [REDACTED]"
    assert result.counts == {"authorization_header": 1}


def test_docker_config_auth(redactor: SecretRedactor) -> None:
    line = '{"auths": {"registry.example.com": {"auth": "dXNlcjpwYXNz"}}}'

    result = redactor.redact(line)

    assert result.text == '{"auths": {"registry.example.com": {"auth": "[REDACTED]"}}}'
    assert result.counts == {"docker_auth_json": 1}


def test_bare_jwt(redactor: SecretRedactor) -> None:
    jwt = (
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
        ".eyJzdWIiOiIxMjM0In0"
        ".SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJV"
    )

    result = redactor.redact(f"kubectl token: {jwt} expired")

    assert result.text == f"kubectl token: {REDACTED} expired"
    assert result.counts == {"jwt": 1}


@pytest.mark.parametrize(
    "token",
    [
        "ghp_" + "a" * 36,
        "github_pat_" + "b" * 30,
        "glpat-" + "c" * 20,
        "xoxb-1234567890-abcdefghij",
        "sk-" + "d" * 40,
        "AKIAIOSFODNN7EXAMPLE",
        "AIza" + "e" * 35,
    ],
)
def test_known_token_prefixes(redactor: SecretRedactor, token: str) -> None:
    result = redactor.redact(f"using {token} for auth")

    assert token not in result.text
    assert result.counts == {"known_token_prefix": 1}


def test_kubeconfig_data_fields_are_redacted(redactor: SecretRedactor) -> None:
    kubeconfig = (
        "clusters:\n"
        "- cluster:\n"
        "    certificate-authority-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUM4akNDQWRxZ0F3\n"
        "    server: https://10.0.0.1:6443\n"
        "users:\n"
        "- user:\n"
        "    client-certificate-data: LS0tLS1CRUdJTiBDRVJUSUZJQ0FURS0tLS0tCk1JSUM4akNDQWRx\n"
        "    client-key-data: LS0tLS1CRUdJTiBSU0EgUFJJVkFURSBLRVktLS0tLQpNSUlFcEFJQkFBS0NBUUVB\n"
    )

    result = redactor.redact(kubeconfig)

    assert "LS0t" not in result.text
    assert "server: https://10.0.0.1:6443" in result.text
    assert f"client-key-data: {REDACTED}" in result.text
    assert result.counts == {"key_value": 3}


def test_bare_aws_key_pair_is_fully_redacted(redactor: SecretRedactor) -> None:
    """The id has a prefix (AKIA), the secret has none: only its 40-char shape gives it away."""
    line = "creds: AKIAIOSFODNN7EXAMPLE / wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"

    result = redactor.redact(line)

    assert result.text == f"creds: {REDACTED} / {REDACTED}"
    assert result.counts == {"known_token_prefix": 1, "aws_secret_key": 1}


@pytest.mark.parametrize(
    "line",
    [
        "commit 3f2a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a39 (HEAD -> main)",  # git SHA-1: 40 hex
        "Digest: sha256:9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
        "image pushed with id 2f1a9c1e8b7d6a5f4e3d2c1b0a9f8e7d6c5b4a3f9e8d7c6b",  # 48 chars
    ],
)
def test_hashes_and_long_blobs_are_not_mistaken_for_aws_secrets(
    redactor: SecretRedactor, line: str
) -> None:
    assert redactor.redact(line).text == line


def test_private_key_block_is_removed_entirely(redactor: SecretRedactor) -> None:
    text = (
        "Loading key\n"
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEA0Z3VS5JJcds3xfn/ygWyF8PbnGy0AH+X\n"
        "d3dfsdf3rfsdfsdfsdfsdf\n"
        "-----END RSA PRIVATE KEY-----\n"
        "Key loaded"
    )

    result = redactor.redact(text)

    assert result.text == "Loading key\n[REDACTED PRIVATE KEY]\nKey loaded"
    assert result.counts == {"private_key_block": 1}


# ---------------------------------------------------------------------------
# Behaviour of the redactor as a whole
# ---------------------------------------------------------------------------


def test_text_without_secrets_is_returned_unchanged(redactor: SecretRedactor) -> None:
    log = (
        "[INFO] Building payment-service 1.4.2\n"
        "[ERROR] Failed to execute goal on project payment-service: "
        "Could not resolve dependencies for project com.example:payment-service:jar:1.4.2\n"
        "[ERROR] status code: 401, reason phrase: Unauthorized (401)"
    )

    result = redactor.redact(log)

    assert result.text == log
    assert result.counts == {}
    assert result.total == 0


def test_bearer_jwt_is_counted_once(redactor: SecretRedactor) -> None:
    """The header pattern runs first; the JWT pattern must not double count."""
    line = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.abc123def456xyz"

    result = redactor.redact(line)

    assert result.text == "Authorization: Bearer [REDACTED]"
    assert result.counts == {"authorization_header": 1}
    assert result.total == 1


def test_multiline_log_with_mixed_secrets(redactor: SecretRedactor) -> None:
    log = (
        "+ export DOCKER_PASSWORD=p4ssw0rd\n"
        "+ docker login -u ci --password p4ssw0rd registry.example.com\n"
        "+ curl -H 'Authorization: Bearer abc.def.ghi' https://api.example.com\n"
        "+ mvn deploy -s https://ci:tok3n@artifactory.example.com/repo\n"
        "[ERROR] Return code is: 401, ReasonPhrase: Unauthorized."
    )

    result = redactor.redact(log)

    for secret in ("p4ssw0rd", "abc.def.ghi", "tok3n"):
        assert secret not in result.text
    assert "[ERROR] Return code is: 401, ReasonPhrase: Unauthorized." in result.text
    assert result.total == 4


def test_custom_pattern_set_can_be_injected() -> None:
    redactor = SecretRedactor(patterns=())

    result = redactor.redact("PASSWORD=visible")

    assert result.text == "PASSWORD=visible"
    assert result.counts == {}
