import re

from fastapi.testclient import TestClient


def _sample(client: TestClient, name: str) -> str:
    """The lines of one metric family from the scrape output."""
    body = client.get("/metrics").text
    return "\n".join(line for line in body.splitlines() if line.startswith(name))


def _value(client: TestClient, series: str) -> float:
    match = re.search(re.escape(series) + r" (\S+)", client.get("/metrics").text)
    return float(match.group(1)) if match else 0.0


def test_metrics_endpoint_speaks_prometheus_text_format(client: TestClient) -> None:
    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "# TYPE http_requests_total counter" in response.text
    assert "# TYPE http_request_duration_seconds histogram" in response.text


def test_every_request_is_counted_by_route_and_status(client: TestClient) -> None:
    series = 'http_requests_total{method="GET",path="/health",status="200"}'
    before = _value(client, series)

    client.get("/health")
    client.get("/health")

    assert _value(client, series) == before + 2


def test_unknown_paths_do_not_create_new_series(client: TestClient) -> None:
    """Raw URLs are user input; only route templates may become labels."""
    client.get("/nope-1")
    client.get("/nope-2")

    families = _sample(client, "http_requests_total")
    assert 'path="/nope-1"' not in families
    assert 'path="unmatched",status="404"' in families


def test_duration_histogram_observes_the_request(client: TestClient) -> None:
    series = 'http_request_duration_seconds_count{method="GET",path="/health"}'
    before = _value(client, series)

    client.get("/health")

    assert _value(client, series) == before + 1
