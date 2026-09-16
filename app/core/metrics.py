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

ANALYSES = Counter(
    "analysis_total",
    "Analyses attempted, by outcome (success, llm_unavailable, llm_invalid_response).",
    ["outcome"],
)

ANALYSIS_CATEGORIES = Counter(
    "analysis_category_total",
    "Final categories returned to callers, by category and rule agreement.",
    ["category", "agreement"],  # agreement: agree | disagree | no_rule_opinion
)

LLM_REQUEST_DURATION = Histogram(
    "llm_request_duration_seconds",
    "Round-trip time of one model call, including SDK retries.",
    ["provider"],
    buckets=(0.5, 1, 2, 5, 10, 20, 30, 60, 120),
)

LLM_TOKENS = Counter(
    "llm_tokens_total",
    "Tokens billed by the model provider, by direction (input, output).",
    ["provider", "direction"],
)

SECRETS_REDACTED = Counter(
    "secrets_redacted_total",
    "Secrets removed before anything left the process, by redaction pattern.",
    ["pattern"],
)

EXCERPT_TOKENS = Histogram(
    "analysis_excerpt_tokens",
    "Estimated size of the excerpt sent to the model; drives cost and truncation tuning.",
    buckets=(250, 500, 1000, 1500, 2000, 2500, 3000, 4000),
)
