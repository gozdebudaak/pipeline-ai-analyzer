"""Prometheus metrics.

Logs answer "what happened in request X". Metrics answer "how is the service
doing over the last five minutes": request rate, error ratio, latency
percentiles. They are cheap numbers, aggregated in the process, and *pulled*
by Prometheus from ``GET /metrics`` on its own schedule, so the service never
needs to know where the monitoring system lives.

Rules for labels: every distinct label combination is a separate time series
kept in memory forever. Labels must therefore come from a small fixed set
(route template, status code), never from user input (raw path, request id).
"""

from prometheus_client import Counter, Histogram

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests handled, by route template and status code.",
    ["method", "path", "status"],
)

HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Wall-clock time to handle one HTTP request.",
    ["method", "path"],
    # An LLM call takes seconds; health checks take milliseconds. Buckets must cover both.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
)
