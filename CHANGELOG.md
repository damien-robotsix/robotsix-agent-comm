# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Automated CI/CD release pipeline (`.github/workflows/publish.yml`):
  tag-triggered (`v*`) publish to PyPI and a `workflow_dispatch`
  TestPyPI dry-run, using Trusted Publishing (OIDC), with a
  tag/version guard and a CHANGELOG-driven GitHub Release. Mirrored
  into `templates/python-package/`.
- Release procedure documentation (`docs/publishing/releasing.md`)
  covering one-time operator setup, per-release steps, and the
  Trusted-Publishing / API-token credential model.

## [0.1.0] - 2026-06-14

### Added

- Core messaging protocol layer (`robotsix_agent_comm.protocol`):
  message types, JSON serialization, and validation.
- Network transport layer (`robotsix_agent_comm.transport`): HTTP/JSON
  client and server, agent registry, router, retry policy, and endpoint
  definitions.
- High-level synchronous SDK (`robotsix_agent_comm.sdk`) exposing the
  `Agent` client, with runnable examples under `examples/` and tutorial
  documentation.
