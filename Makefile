.PHONY: install lint format typecheck test test-unit test-integration test-e2e ci up down logs migrate

install:
	uv sync

lint:
	uv run ruff check .

format:
	uv run black .
	uv run ruff check --fix .

typecheck:
	uv run mypy .

test:
	uv run pytest

test-unit:
	uv run pytest tests/unit

test-integration:
	uv run pytest tests/integration

test-e2e:
	uv run pytest tests/e2e

ci: lint typecheck test

up:
	docker-compose up

down:
	docker-compose down

logs:
	docker-compose logs -f

migrate:
	uv run alembic upgrade head
