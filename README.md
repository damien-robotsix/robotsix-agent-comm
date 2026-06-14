# robotsix-agent-comm

Agent communication system for the robotsix ecosystem.

## Overview

`robotsix-agent-comm` provides the agent communication layer for the
robotsix ecosystem — the foundation on which the messaging protocol and
transport layer are built.

## Status

Early scaffold. This repository currently contains only the project
foundation (packaging, tooling, CI, and documentation skeleton). The
messaging protocol and transport layer land in later phases and ship as
separate changes.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management. The lockfile (`uv.lock`) is committed; CI installs with
`uv sync --frozen`.

```bash
uv sync
```

### Running checks

```bash
ruff check .         # lint
ruff format --check .  # formatting check
mypy .               # static type checking (strict)
pytest               # tests
```

You can also run any of these through uv, e.g. `uv run pytest`.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and
contribution guidelines. Project documentation lives under
[`docs/`](docs/).
