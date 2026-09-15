from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.llm.base import LLMUnavailableError
from app.llm.fake import FakeLLMProvider
from app.main import build_analysis_service
from app.schemas.analyze import MAX_LOG_CHARS
from app.services.analysis_service import AnalysisService
from app.services.failure_classifier import FailureClassifier
from app.services.log_processor import LogProcessor
from app.services.prompt_builder import PromptBuilder
from app.services.secret_redactor import SecretRedactor

SAMPLE = (
    Path(__file__).resolve().parents[2] / "sample_logs" / "maven" / "dependency_resolution_401.log"
)
PLANTED_KEY = "AKCp8k3Jd9sLx2mQw7vB4nH6tY1uZ0pR"


def _install_provider(client: TestClient, provider: FakeLLMProvider) -> None:
    client.app.state.analysis_service = AnalysisService(  # type: ignore[attr-defined]
        redactor=SecretRedactor(),
        processor=LogProcessor(),
        classifier=FailureClassifier(),
        prompt_builder=PromptBuilder(),
        provider=provider,
    )


# ---------------------------------------------------------------------------
# happy path
# ---------------------------------------------------------------------------


def test_analyze_returns_structured_result(client: TestClient) -> None:
    raw = SAMPLE.read_text()

    response = client.post(
        "/api/v1/analyze",
        json={
            "log": raw,
            "source": "jenkins",
            "metadata": {"job": "payment-service", "build": "142"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["analysis"]["category"] == "authentication"
    assert 0.0 <= body["analysis"]["confidence"] <= 1.0
    assert body["analysis"]["suggested_actions"]
    assert body["rule_based"]["category"] == "authentication"
    assert body["rule_based"]["agrees_with_model"] is True
    assert "http_401" in body["rule_based"]["matched_rules"]
    assert body["log_stats"]["secrets_redacted"] >= 1
    assert body["llm"]["provider"] == "fake"
    assert body["source"] == "jenkins"
    assert body["metadata"] == {"job": "payment-service", "build": "142"}
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_response_never_contains_the_planted_secret(client: TestClient) -> None:
    raw = SAMPLE.read_text()
    assert PLANTED_KEY in raw

    response = client.post("/api/v1/analyze", json={"log": raw})

    assert PLANTED_KEY not in response.text


# ---------------------------------------------------------------------------
# request validation (FastAPI -> 422 before the service is touched)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {},  # log missing
        {"log": ""},  # empty
        {"log": "x" * (MAX_LOG_CHARS + 1)},  # too large
        {"log": "ok", "unexpected": 1},  # unknown field
        {"log": "ok", "metadata": {"build": 142}},  # metadata values must be strings
    ],
)
def test_invalid_requests_are_rejected(client: TestClient, payload: dict[str, object]) -> None:
    response = client.post("/api/v1/analyze", json=payload)

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# provider failures -> standard error envelope
# ---------------------------------------------------------------------------


def test_llm_outage_is_503(client: TestClient) -> None:
    _install_provider(client, FakeLLMProvider(error=LLMUnavailableError("rate limited")))

    response = client.post("/api/v1/analyze", json={"log": "[ERROR] boom"})

    assert response.status_code == 503
    body = response.json()
    assert body["error"]["code"] == "llm_unavailable"
    assert body["error"]["request_id"] == response.headers["X-Request-ID"]


def test_llm_garbage_is_502(client: TestClient) -> None:
    _install_provider(client, FakeLLMProvider(raw_output="not json"))

    response = client.post("/api/v1/analyze", json={"log": "[ERROR] boom"})

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "llm_invalid_response"


def test_unknown_path_uses_the_same_error_envelope(client: TestClient) -> None:
    response = client.get("/nope")

    assert response.status_code == 404
    assert set(response.json()["error"]) == {"code", "message", "request_id"}


# ---------------------------------------------------------------------------
# no LLM configured: alive but not ready
# ---------------------------------------------------------------------------


def test_without_llm_the_app_is_alive_but_not_ready(client: TestClient) -> None:
    client.app.state.analysis_service = None  # type: ignore[attr-defined]

    assert client.get("/health").status_code == 200
    ready = client.get("/ready")
    assert ready.status_code == 503
    assert ready.json() == {"status": "not_ready", "checks": {"llm": "not_configured"}}

    analyze = client.post("/api/v1/analyze", json={"log": "[ERROR] boom"})
    assert analyze.status_code == 503
    assert analyze.json()["error"]["code"] == "llm_not_configured"


def test_build_analysis_service_returns_none_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.config import Settings

    settings = Settings(_env_file=None, app_env="development", openai_api_key=None)

    assert build_analysis_service(settings) is None
