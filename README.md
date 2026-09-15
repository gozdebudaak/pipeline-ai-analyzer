# pipeline-ai-analyzer

[![CI](https://github.com/gozdebudaak/pipeline-ai-analyzer/actions/workflows/ci.yml/badge.svg)](https://github.com/gozdebudaak/pipeline-ai-analyzer/actions/workflows/ci.yml)

AI-powered DevOps assistant that analyzes CI/CD pipeline failures, extracts the
relevant error sections from logs, identifies probable root causes and suggests
remediation steps. Designed as a production-oriented platform component, not a
thin LLM wrapper.

> Status: **MVP 0 — project foundation** (no AI functionality yet).

## Roadmap

| Milestone | Scope |
|-----------|-------|
| MVP 0 | FastAPI skeleton, config, structured logging, health/ready, Docker, CI |
| MVP 1 | `POST /api/v1/analyze`, secret redaction, log preprocessing, OpenAI provider |
| MVP 2 | Deterministic failure rules, severity/confidence, token optimization |
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
```

`uv run <cmd>` executes `<cmd>` inside the project's virtual environment, so
you never need to activate `.venv` manually.

## Running with Docker

```bash
docker compose up --build        # build the image and start the API on :8000
docker compose ps                # STATUS should show "(healthy)"
curl -i http://127.0.0.1:8000/health
docker compose down
```

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
  api/middleware.py   correlation ID + request logging
  api/routes/         HTTP endpoints
  core/config.py      settings from environment variables (pydantic-settings)
  core/logging.py     JSON log formatter with correlation ID
  main.py             FastAPI application factory
tests/
  conftest.py         shared fixtures (TestClient)
  unit/               fast, isolated tests
  integration/        tests that exercise the HTTP layer
.github/workflows/    CI pipeline
```

Further directories (`services/`, `llm/`, `integrations/`, `migrations/`,
`helm/`) are added in the milestone that first needs them.
