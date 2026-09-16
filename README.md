# pipeline-ai-analyzer

[![CI](https://github.com/gozdebudaak/pipeline-ai-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/gozdebudaak/pipeline-ai-analyzer/actions/workflows/ci.yml)

AI-powered DevOps assistant that analyzes CI/CD pipeline failures, extracts the
relevant error sections from logs, identifies probable root causes and suggests
remediation steps. Designed as a production-oriented platform component, not a
thin LLM wrapper.

> Status: **MVP 1 — AI log analyzer** is implemented: `POST /api/v1/analyze`
> takes a raw CI/CD log and returns a validated, structured analysis. The
> pipeline is secret redaction → log preprocessing → rule-based
> classification → LLM analysis → schema validation → reconciliation.

## Roadmap

| Milestone | Scope |
|-----------|-------|
| MVP 0 | FastAPI skeleton, config, structured logging, health/ready, Docker, CI |
| MVP 1 | `POST /api/v1/analyze`, secret redaction, log preprocessing, OpenAI provider |
| MVP 2 | Smarter preprocessing, rule-based severity, wider redaction, evaluation set, Prometheus metrics |
| MVP 3 | Jenkins integration (REST + webhooks) |
| MVP 4 | PostgreSQL persistence, analysis history, Helm chart |

## Requirements

| Tool | Version | Purpose |
|------|---------|---------|
| [uv](https://docs.astral.sh/uv/) | ≥ 0.5 | Package and Python version manager |
| Python | 3.12 | Installed automatically by uv |
| Docker Desktop | ≥ 24 | Running the full stack locally |

### Installing uv (macOS / Linux)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source ~/.zshrc          # or open a new terminal
uv --version
```

`uv` installs into `~/.local/bin` and does not touch the system Python.
It will download the Python version pinned in `.python-version` on demand:

```bash
uv python install 3.12
```

## How an analysis works

```text
POST /api/v1/analyze {"log": "..."}
        │
        ▼
 SecretRedactor        named patterns (URL creds, headers, JWT, AWS/GCP/kubeconfig,
                       webhooks) + a high-entropy safety net -> [REDACTED]
        │
        ▼
 LogProcessor          strip ANSI/timestamps, mark long pauses, drop noise,
                       fold repeats and near-repeats, keep error regions +
                       context + tail within a line and token budget
        │
        ├──────────────► FailureClassifier   weighted rule table -> category + severity
        ▼
 PromptBuilder         system rules + <log_excerpt>  (no classifier hint)
        │
        ▼
 LLMProvider           OpenAI (structured output) or the fake provider in tests
        │
        ▼
 AnalysisResult        strict Pydantic schema; invalid model output never leaves the API
        │
        ▼
 reconcile()           model category vs rule-based category -> confidence adjusted
                       (x0.7 on disagreement); severity disagreement is reported, not penalised
```

Nothing derived from the log reaches the LLM before it has passed the
redactor, and the rule-based opinion is deliberately withheld from the model
so the two can be compared as independent signals.

## Local development

```bash
git clone <repo-url> pipeline-ai-analyzer
cd pipeline-ai-analyzer
uv sync                  # creates .venv and installs locked dependencies
cp .env.example .env
```

Common commands (see the `Makefile`; each target runs exactly what CI runs):

```bash
make run          # start the API on http://127.0.0.1:8000 with auto-reload
make test         # pytest
make lint         # ruff check + format check
make format       # auto-fix lint issues and reformat
make typecheck    # mypy
make check        # lint + typecheck + test
make eval         # score the rule-based chain on the labelled sample logs (offline)
make eval-llm     # same, plus the configured model's answer per case (costs money)
```

`uv run <cmd>` executes `<cmd>` inside the project's virtual environment, so
you never need to activate `.venv` manually.

## Using the API

Start the server (see below), then:

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/analyze \
  -H 'Content-Type: application/json' \
  -d '{"log": "[ERROR] Return code is: 401, ReasonPhrase: Unauthorized.", "source": "jenkins", "metadata": {"job": "payment-service", "build": "142"}}'
```

Response (abridged):

```json
{
  "request_id": "69d0db29-...",
  "analysis": {
    "status": "failed", "category": "authentication", "severity": "high",
    "summary": "...", "root_cause": "...", "confidence": 0.9,
    "evidence": ["401 Unauthorized"],
    "suggested_actions": ["Verify the repository credentials configured in the pipeline."]
  },
  "rule_based": {"category": "authentication", "matched_rules": ["http_401"], "agrees_with_model": true},
  "log_stats": {"total_lines": 57, "normalized_lines": 33, "excerpt_lines": 33, "estimated_tokens": 640, "truncated": false, "secrets_redacted": 4},
  "llm": {"provider": "openai", "model": "gpt-5.4-mini", "input_tokens": 1512, "output_tokens": 290, "prompt_version": "1"}
}
```

| Endpoint | Purpose |
|----------|---------|
| `POST /api/v1/analyze` | Analyse a raw log (body: `log`, optional `source`, `metadata`) |
| `GET /health` | Liveness: the process is up |
| `GET /ready` | Readiness: `503` with `{"llm": "not_configured"}` when no provider is set |
| `GET /docs` | Interactive OpenAPI documentation |

Errors share one envelope, `{"error": {"code", "message", "request_id"}}`:

| Status | `code` | Meaning |
|--------|--------|---------|
| 422 | — | Request body invalid (empty log, over 2 M characters, unknown field) |
| 502 | `llm_invalid_response` | The model answered but the answer failed schema validation |
| 503 | `llm_unavailable` | Provider unreachable, rate limited or out of credit |
| 503 | `llm_not_configured` | `OPENAI_API_KEY` is not set |

### Configuration

| Variable | Default | Purpose |
|----------|---------|---------|
| `APP_ENV` | `development` | `development`, `test` or `production`. In `test` the fake provider is always used. |
| `LOG_LEVEL` | `INFO` | Root log level |
| `OPENAI_API_KEY` | — | Required for real analyses; never logged (`SecretStr`) |
| `OPENAI_MODEL` | `gpt-5.4-mini` | Any chat-completions model with structured output support |
| `LLM_TIMEOUT_SECONDS` | `60` | Per-request timeout |
| `LLM_MAX_RETRIES` | `2` | SDK retries for transient failures only |

Run without touching OpenAI (the fake provider returns a canned analysis):

```bash
APP_ENV=test make run
```

## Evaluation

`evaluation/cases.json` holds one human-written label per sample log:
expected category and severity, a key phrase that must survive into the
excerpt, and the planted secrets that must not leak. The integration tests
and `make eval` read the same file, so the two can never disagree.

```bash
make eval
```

```text
case                     category   severity  phrase  tokens  rules
maven-401                ok         ok        ok         571  maven_dependency, artifactory, http_401
...
cases:             11
category accuracy: 100%
severity accuracy: 100%
key phrase kept:   100%
secret leaks:      0
```

Accuracy is a gauge, not a gate: a drop prints and exits 0 so a human can
decide. A leaked secret exits 1. A case the rules are known to get wrong
carries `rule_based_expected`; the test pins that value, the score still
counts it as a miss and the report marks it `(known)`.

`make eval-llm` runs every case through the model as well and prints the
model's accuracy, the number of rule/model disagreements and who was right
in each: the data behind the 0.7 confidence factor. Under `APP_ENV=test` it
uses the fake provider; otherwise it calls the real model and costs money.

## Observability

Every request is logged as JSON with a correlation ID (`X-Request-ID`).
`GET /metrics` exposes Prometheus metrics (pull model: nothing is pushed):

| Metric | Labels | Answers |
|--------|--------|---------|
| `http_requests_total` | method, route template, status | request rate, error ratio |
| `http_request_duration_seconds` | method, route template | latency percentiles |
| `analysis_total` | outcome | success vs `llm_unavailable` / `llm_invalid_response` |
| `analysis_category_total` | category, agreement | what is failing; rule/model disagreement rate |
| `llm_request_duration_seconds` | provider | model latency, including failed calls |
| `llm_tokens_total` | provider, direction | the bill |
| `secrets_redacted_total` | pattern | the redactor at work |
| `analysis_excerpt_tokens` | – | excerpt size vs the 3000-token budget |

Labels only ever come from fixed sets (route templates, enums); raw URLs
would create an unbounded number of time series. To see the numbers on a
dashboard start the optional Prometheus service:

```bash
docker compose --profile monitoring up --build
open http://127.0.0.1:9090      # Status -> Targets should show pipeline-ai-analyzer UP
```

Example queries: `rate(http_requests_total[5m])`,
`histogram_quantile(0.95, rate(llm_request_duration_seconds_bucket[5m]))`,
`sum(rate(llm_tokens_total[1h])) by (direction)`.

## Running with Docker

```bash
docker compose up --build        # build the image and start the API on :8000
docker compose ps                # STATUS should show "(healthy)"
curl -i http://127.0.0.1:8000/health
docker compose down
```

Compose reads `OPENAI_API_KEY` and `OPENAI_MODEL` from `.env`. Without a
key the container is still healthy (liveness) but `/ready` returns 503 and
`/analyze` answers `llm_not_configured`.

The image is a two-stage build: dependencies are installed with `uv` in a
builder stage, and only the resulting virtual environment and source are
copied into a `python:3.12-slim` runtime image that runs as the unprivileged
`app` user. A `HEALTHCHECK` polls `/health`; Compose reports the container
as healthy once it passes.

> macOS note: if `docker` is not found after installing Docker Desktop, open
> Docker Desktop → Settings → Advanced and enable the system-wide CLI
> symlinks, or add `/Applications/Docker.app/Contents/Resources/bin` to your
> `PATH` in `~/.zshrc`.

## Continuous integration

Every push and pull request to `main` runs the workflow in
`.github/workflows/ci.yml`: `lint`, `typecheck` and `test` run in parallel;
`docker-build` runs only when all three pass. The workflow uses `uv sync`
with `UV_FROZEN=1`, so CI installs exactly what `uv.lock` pins.

## Project layout

```text
app/
  api/routes/analyze.py        POST /api/v1/analyze (thin: validate, call the service, map)
  api/routes/health.py         /health and /ready
  api/errors.py                domain exceptions -> standard error envelope
  api/dependencies.py          how routes obtain the analysis service
  api/middleware.py            correlation ID + request logging + request metrics
  api/routes/metrics.py        GET /metrics (Prometheus text format)
  core/metrics.py              metric definitions (counters, histograms) and the label rules
  core/config.py               settings from environment variables (pydantic-settings)
  core/logging.py              JSON log formatter with correlation ID
  schemas/analysis.py          AnalysisResult: the strict result schema
  schemas/analyze.py           request/response bodies of the analyze endpoint
  services/secret_redactor.py  removes passwords, tokens, keys before anything reaches an LLM
  services/log_processor.py    normalises logs and extracts error regions within a budget
  services/failure_classifier.py  weighted rule table mapping error lines to a category
  services/prompt_builder.py   system + user messages sent to the model
  services/analysis_service.py orchestration, rule-vs-model reconciliation, domain metrics
  evaluation/dataset.py        EvalCase labels: the single source of truth for sample logs
  evaluation/runner.py         offline and LLM evaluation runs, plain-text report
  llm/base.py                  LLMProvider contract, error types, output validation gate
  llm/openai_provider.py       OpenAI implementation (structured output, error mapping)
  llm/fake.py                  no-network provider for tests and local runs
  llm/factory.py               picks the provider from settings; test env always gets the fake
  main.py                      FastAPI application factory
sample_logs/                   realistic failure logs (Maven, Gradle, npm, pip, Docker, Kubernetes, Helm, Artifactory)
evaluation/cases.json          human labels for every sample log (used by tests and make eval)
deploy/prometheus/             scrape config for the optional Prometheus service
                               with embedded fake secrets; used by the chain tests
tests/
  conftest.py                  shared fixtures (TestClient)
  unit/                        fast, isolated tests (one file per service)
  integration/                 HTTP layer tests and the sample-log chain test
.github/workflows/             CI pipeline
```

Further directories (`services/`, `llm/`, `integrations/`, `migrations/`,
`helm/`) are added in the milestone that first needs them.
