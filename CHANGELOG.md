# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Extracted `_TokenBucket` and `_AuditLogger` helpers from `broker/server.py`
  into dedicated `broker/_rate_limit.py` and `broker/_audit.py` modules for
  improved cohesion and independent testability.

### Added

- `robotsix_agent_comm.sdk.reply.reply_text()`: a pure, dependency-free
    extractor that returns the `"reply"` string from a brokered response
    body with a configurable fallback.
- `robotsix_agent_comm.sdk.brokered_request.BrokeredRequester`: a one-shot,
    per-call helper that encapsulates the brokered request lifecycle
    (transport pair, agent, send, error unwrap, reply extraction) so
    consumers no longer need to open-code it.

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
- Automated CI/CD release pipeline (`.github/workflows/publish.yml`):
    tag-triggered (`v*`) publish to PyPI and a `workflow_dispatch`
    TestPyPI dry-run, using Trusted Publishing (OIDC), with a
    tag/version guard and a CHANGELOG-driven GitHub Release. Mirrored
    into `templates/python-package/`.
- Release procedure documentation (`docs/publishing/releasing.md`)
    covering one-time operator setup, per-release steps, and the
    Trusted-Publishing / API-token credential model.
