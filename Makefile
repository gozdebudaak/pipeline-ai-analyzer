.PHONY: install lint format typecheck test check run docker-up docker-down

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

run:
	uv run uvicorn app.main:app --reload

docker-up:
	docker compose up --build

docker-down:
	docker compose down
