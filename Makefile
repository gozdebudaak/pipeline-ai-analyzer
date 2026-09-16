.PHONY: install lint format typecheck test check eval eval-llm run docker-up docker-down

install:
	uv sync --group dev

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy app tests

test:
	APP_ENV=test uv run pytest -v

check: lint typecheck test   ## everything CI checks, in one go

eval:  ## score the rule-based chain on evaluation/cases.json (offline, no LLM)
	uv run python -m app.evaluation

eval-llm:  ## same, plus the configured model's answer per case (costs money outside APP_ENV=test)
	uv run python -m app.evaluation --llm

run:
	uv run uvicorn app.main:app --reload

docker-up:
	docker compose up --build

docker-down:
	docker compose down
