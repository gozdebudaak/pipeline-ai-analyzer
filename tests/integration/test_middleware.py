from fastapi.testclient import TestClient

from app.api.middleware import REQUEST_ID_HEADER
from app.core.logging import correlation_id_var


def test_response_carries_a_generated_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


def test_incoming_request_id_is_echoed_back(client: TestClient) -> None:
    response = client.get("/health", headers={REQUEST_ID_HEADER: "jenkins-build-142"})

    assert response.headers[REQUEST_ID_HEADER] == "jenkins-build-142"


def test_invalid_incoming_request_id_is_replaced(client: TestClient) -> None:
    too_long = "x" * 129
    with_newline = "abc\\ndef"

    for bad in (too_long, with_newline, "spaces are bad", ""):
        response = client.get("/health", headers={REQUEST_ID_HEADER: bad})
        assert response.headers[REQUEST_ID_HEADER] != bad
        assert len(response.headers[REQUEST_ID_HEADER]) == 36  # a UUID was generated


def test_each_request_gets_a_distinct_id(client: TestClient) -> None:
    first = client.get("/health").headers[REQUEST_ID_HEADER]
    second = client.get("/health").headers[REQUEST_ID_HEADER]

    assert first != second


def test_context_is_cleared_after_the_request(client: TestClient) -> None:
    client.get("/health")

    assert correlation_id_var.get() is None
