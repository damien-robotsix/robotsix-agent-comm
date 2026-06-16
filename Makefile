.PHONY: install test lint format

install:
	uv sync

test:
	uv run pytest

lint:
	uv run ruff check src tests
	uv run mypy src tests

format:
	uv run ruff format src tests
	uv run ruff check --fix src tests
