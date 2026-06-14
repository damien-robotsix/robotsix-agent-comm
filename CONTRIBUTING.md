# Contributing to robotsix-agent-comm

Thanks for your interest in contributing! This document covers the
development setup and the conventions this repository follows.

## Development setup

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency and
environment management, and targets Python 3.12+.

```bash
git clone https://github.com/robotsix/robotsix-agent-comm.git
cd robotsix-agent-comm
uv sync
```

The lockfile (`uv.lock`) is committed to the repository, and CI installs
dependencies with `uv sync --frozen`. When you change dependencies in
`pyproject.toml`, regenerate the lockfile with `uv lock` and commit the
result. **Never hand-edit `uv.lock`.**

## Checks

Run the full set of checks locally before opening a pull request:

```bash
uv run ruff check .         # lint
uv run ruff format --check .  # formatting
uv run mypy .               # static type checking (strict)
uv run pytest               # tests
```

`uv run ruff format .` rewrites files to the canonical format.

## Branches and pull requests

- Create a feature branch off `main` for your change.
- Keep changes focused and accompanied by tests where applicable.
- Ensure all checks above pass before requesting review.
- Open a pull request against `main`; CI runs the same checks.

## Architecture decisions

Architecture Decision Records (ADRs) live under
[`docs/decisions/`](docs/decisions/). This repository follows a
stdlib-first / minimal-dependency philosophy — see
[`docs/decisions/0001-stdlib-first.md`](docs/decisions/0001-stdlib-first.md).
