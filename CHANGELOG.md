# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Broker: embedded traffic recorder (bounded, thread-safe ring buffer) and
    ``GET /traffic`` JSON endpoint with ``agent``, ``topic``, ``since``,
    ``until``, and ``limit`` query filters.  ``GET /agents`` now includes
    ``last_seen_seconds_ago``, ``ttl_seconds``, ``status``, and ``mailbox``
    for each registered agent.

- `AGENT.md` — project overview for AI coding agents covering repo identity,
    key directories, build/test/lint commands, environment variables, periodic
    workflows, and notable omissions.

- `robotsix_agent_comm.sdk.reply.reply_text()`: a pure, dependency-free
    extractor that returns the `"reply"` string from a brokered response
    body with a configurable fallback.

- `robotsix_agent_comm.sdk.brokered_request.BrokeredRequester`: a one-shot,
    per-call helper that encapsulates the brokered request lifecycle
    (transport pair, agent, send, error unwrap, reply extraction) so
    consumers no longer need to open-code it.

### Changed

- Created `tests/conftest.py` with shared `broker` and `agent_server`
    fixtures, eliminating triplicate definitions across broker and SDK
    test files.

- Enabled ruff docstring linting rules (D-prefix) following the Google
    convention, with per-file ignores for D10 codes in tests and
    vulture_whitelist, and D107 in broker/server.py.

- Extracted `_TokenBucket` and `_AuditLogger` helpers from `broker/server.py`
    into dedicated `broker/_rate_limit.py` and `broker/_audit.py` modules for
    improved cohesion and independent testability.

- `Agent._build_metadata_and_body()`: extracted shared message-construction
    logic from `send_request` and `send_notification` into a private helper.

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
