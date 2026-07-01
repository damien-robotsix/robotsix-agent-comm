# PyPI publishing standards

Publication to PyPI is **selective**, not automatic. The robotsix
ecosystem publishes only mature, well-documented repositories whose
quality justifies a public release and ongoing maintenance commitment.
Quality and long-term maintainability take priority over publishing
every repository; a repo is published only once it meets *every*
standard below. These standards apply fleet-wide, and
`robotsix-agent-comm` itself serves as the reference implementation —
each section cites the matching artifact in this repository.

Use the companion [pre-publication checklist](checklist.md) as the
verifiable gate a maintainer ticks through before publishing a repo.

Once a package is published, its ongoing lifecycle (versioning policy,
breaking changes/deprecations, security response, and retiring a
package) lives in the
[package maintenance procedures](maintenance.md) runbook.

## Project structure & layout

Published packages MUST use the `src/` layout: the importable package
lives under `src/<package>/`, and `tests/` mirrors the package layout.
The build backend MUST be Hatchling, and the environment MUST be managed
with [`uv`](https://docs.astral.sh/uv/) with a committed `uv.lock` so
installs are reproducible (`uv sync --frozen`).

Reference: this repo's `pyproject.toml` declares

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/robotsix_agent_comm"]
```

## Required metadata

The `pyproject.toml` `[project]` table MUST declare all of: `name`,
`version`, `description`, `readme`, `requires-python` (`>=3.12`),
`license`, `authors`, `dependencies`, and `classifiers`. The
`classifiers` list MUST include an OSI-approved license classifier
(`License :: OSI Approved :: ...`), the supported
`Programming Language :: Python :: 3.x` versions, `Typing :: Typed`, and
an accurate `Development Status`.

Reference: this repo's `[project]` block —

```toml
[project]
name = "robotsix-agent-comm"
dynamic = ["version"]
description = "Agent communication system for the robotsix ecosystem"
readme = "README.md"
requires-python = ">=3.12"
license = { text = "MIT" }
authors = [{ name = "Damien Robotsix", email = "damien.robotsix@gmail.com" }]
dependencies = []
classifiers = [
    "Development Status :: 2 - Pre-Alpha",
    "Intended Audience :: Developers",
    "License :: OSI Approved :: MIT License",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.12",
    "Programming Language :: Python :: 3.13",
    "Programming Language :: Python :: 3.14",
    "Typing :: Typed",
]
```

## README & documentation standards

Every published repo MUST have a top-level `README.md` covering the
project overview, current status, install/development instructions, and
a pointer to the contributing guide. It MUST also ship a `docs/` site
built with mkdocs + mkdocs-material + mkdocstrings, and **every** page
MUST be wired into `mkdocs.yml` `nav` (this fleet uses mkdocs nav, not a
`docs/modules.yaml`).

Reference: this repo's [`README.md`](../../README.md) and `mkdocs.yml`.

## Version numbering

Versions MUST follow [Semantic Versioning](https://semver.org/)
(`MAJOR.MINOR.PATCH`). Packages that have not stabilized their public
API SHOULD stay pre-1.0 and signal maturity through an accurate
`Development Status` classifier (e.g. `2 - Pre-Alpha`). The version MUST
have a single source of truth in the git tag (hatch-vcs derives the
version at build time).

## Changelog

Each published repo MUST maintain a `CHANGELOG.md` in the repo root
following [Keep a Changelog](https://keepachangelog.com/) conventions.
It MUST be updated on every release and MUST keep an `Unreleased`
section between releases to accumulate pending changes.

## License file placement

A published repo MUST include a `LICENSE` file in the repo root, and its
contents MUST match the `[project].license` declaration. The fleet
default is MIT.

Reference: this repo's [`LICENSE`](../../LICENSE) matches
`license = { text = "MIT" }` in `pyproject.toml`.

## Contributing guidelines

Every published repo MUST ship a `CONTRIBUTING.md` covering the
development setup (`uv sync`), the local check commands
(`ruff check`, `ruff format --check`, `mypy`, `pytest`), the branch/PR
flow, and the location of Architecture Decision Records.

Reference: this repo's [`CONTRIBUTING.md`](../../CONTRIBUTING.md).

## Minimum test coverage / CI requirements

CI MUST run green on every push and pull request. The pipeline MUST run
ruff (lint **and** format check), mypy in `strict` mode, and pytest with
branch coverage enforced against a minimum threshold. The fleet default
threshold is **85%**, supplied through the shared reusable workflow
`damien-robotsix/robotsix-mill/.github/workflows/python-ci.yml@main`.
Tests MUST set `filterwarnings = ["error"]` so warnings fail the suite.

Reference: this repo's [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)
calls the shared workflow with `coverage-threshold: "85"`, and
`pyproject.toml` sets

```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
filterwarnings = ["error"]
```

## Automated release pipeline

Published packages MUST ship a tag-triggered automated release pipeline
that builds, validates, and publishes to PyPI on `vX.Y.Z` tags using
**PyPI Trusted Publishing (OIDC)** — no PyPI token is stored as a
secret. The workflow MUST also offer a `workflow_dispatch` TestPyPI
dry-run path, enforce the version single-source-of-truth via a
tag/version guard, and cut a CHANGELOG-driven GitHub Release. The full
maintainer procedure lives in [Releasing to PyPI](releasing.md).

Reference: this repo's [`.github/workflows/publish.yml`](../../.github/workflows/publish.yml)
publishes on `v*` tags via Trusted Publishing with a TestPyPI dry-run
path, mirrored into the template skeleton.

## Getting started from the template

New packages SHOULD start from the copy-and-rename skeleton under
[`templates/python-package/`](https://github.com/robotsix/robotsix-agent-comm/tree/main/templates/python-package)
so they satisfy these standards from day one. Copy the directory, rename
`package_name`, and replace the placeholder tokens. A cookiecutter-style
generator is intentionally **not** used — it would add a tool dependency
that conflicts with the stdlib-first ADR 0001; it remains a possible
future option only.
