SHELL := /bin/bash
.DEFAULT_GOAL := help

.PHONY: help
help:  ## Show available targets
	@awk 'BEGIN {FS = ":.*##"; printf "Usage: make \033[36m<target>\033[0m\n\n"} /^[a-zA-Z0-9_-]+:.*?##/ { printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

.PHONY: install
install:  ## Install dev dependencies (uv sync)
	uv sync

.PHONY: test
test:  ## Run tests (fast, no coverage)
	uv run pytest

.PHONY: coverage-report
coverage-report:  ## Run tests with HTML coverage report (two-step: fast terminal first, then HTML)
	uv run pytest --cov=robotsix_agent_comm --cov-report=term-missing
	uv run coverage html

.PHONY: lint
lint:  ## Lint and type-check source
	uv run ruff check src tests
	uv run mypy src tests

.PHONY: format
format:  ## Auto-format source files
	uv run ruff format src tests
	uv run ruff check --fix src tests

.PHONY: typecheck
typecheck:  ## Full-tree strict mypy (matches CI typecheck job)
	uv run mypy src tests

.PHONY: security
security:  ## Local security scan (bandit)
	uv run --with bandit bandit -c pyproject.toml -r src/

.PHONY: docs-serve
docs-serve:  ## Serve docs with live reload
	uv run mkdocs serve

.PHONY: docs-build
docs-build:  ## Build docs in strict mode (CI-style)
	uv run mkdocs build --strict

.PHONY: docker-build
docker-build:  ## Build the production Docker image
	docker build -t robotsix-broker .

.PHONY: clean
clean:  ## Remove build artifacts and cached output
	@echo "Cleaning working directory..."
	@rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov/
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name '*.egg-info' -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name '*.pyc' -delete 2>/dev/null || true
	@rm -rf build/ dist/
	@echo "Done."

.PHONY: check-all
check-all: lint typecheck coverage-report  ## Pre-PR gate: lint, typecheck, and test with coverage

.PHONY: all
all: check-all  ## Alias for check-all
