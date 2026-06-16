#!/bin/bash
set -e
echo "=== ruff check ==="
uv run ruff check .
echo ""
echo "=== ruff format ==="
uv run ruff format --check .
echo ""
echo "=== mypy ==="
uv run mypy .
echo ""
echo "=== pytest ==="
uv run pytest -v
echo ""
echo "=== ALL PASSED ==="
