# package_name

Short description of the package.

## Using this template

This directory is a plain copy-and-rename skeleton for a new robotsix
Python package that already satisfies the
[PyPI publishing standards](../../docs/publishing/index.md). It is **not**
a cookiecutter — there is no generator tool to install (honoring the
stdlib-first ADR 0001). To start a new package:

1. Copy this directory to a new repository.
2. Rename `src/package_name/` to your real package name and update
   `[tool.hatch.build.targets.wheel]` `packages`, `[tool.coverage.run]`
   `source`, and the mkdocstrings paths accordingly.
3. Replace every placeholder token — `package_name`,
   `Package description`, and any `your-org` / author placeholders — in
   `pyproject.toml`, `mkdocs.yml`, `README.md`, and `CONTRIBUTING.md`.
4. Run the [pre-publication checklist](../../docs/publishing/checklist.md)
   before publishing.

## Overview

Describe what the package does and who it is for.

## Status

Early scaffold — update this section as the package matures.

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management. The lockfile (`uv.lock`) is committed; CI installs with
`uv sync --frozen`.

```bash
uv sync
```

### Running checks

```bash
ruff check .           # lint
ruff format --check .  # formatting check
mypy .                 # static type checking (strict)
pytest                 # tests
```

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup and
contribution guidelines. Project documentation lives under `docs/`.
