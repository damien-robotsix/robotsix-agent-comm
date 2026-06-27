# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Docstrings for all 6 HTTP handler methods (`do_GET`, `do_POST`, `do_DELETE`)
    across `_BrokerRequestHandler`, `_StatusRequestHandler`, and
    `_MessageRequestHandler`.

### Removed

- Lifecycle: removed dead-code `__getattr__` forward-compat hook from
    `robotsix_agent_comm.lifecycle`. All 11 public symbols are eagerly imported
    and listed in `__all__`; the hook always raised `AttributeError` and had
    zero reachable call sites.
- Removed stale `__getattr__` entry from `vulture_whitelist.py` (the
    forward-compat hook it suppressed was removed in a prior change).

### Fixed

- `BrokeredResponder.__init__` now accepts and forwards `max_handler_workers`
    (default 4) to `BrokeredAgent`, matching the documented "mirrors BrokeredAgent
    exactly" contract. Previously the parameter was silently dropped.

- Add missing `ROBOTSIX_BROKER_MAILBOX_GRACE_SECONDS` and `ROBOTSIX_BROKER_DASHBOARD_ENABLED` entries to `.env.broker.example` reference config.

- Broker: moved traffic record writes before HTTP response in `_handle_send` to
    eliminate a race condition that caused flaky test failures in
    `test_rejected_sender_mismatch_appears_in_traffic` and
    `test_unknown_recipient_appears_in_traffic`.

### Added

- Lifecycle: new `robotsix_agent_comm.lifecycle` package with `LifecycleServer`,
    `LifecycleConfig`, `build_lifecycle`, and the `robotsix-lifecycle` CLI.
    Provides versioned deployment with rollback support for managed suite
    services: `POST /services/{name}/deploy` (health-gated with auto-rollback),
    `POST /services/{name}/rollback` (explicit or previous revision), and
    `GET /services/{name}/deployments` (ordered history). Includes in-memory
    `DeploymentStore` with per-service locking, a pluggable `LifecycleBackend`
    interface with `SubprocessBackend` for Docker Compose, and 24 tests covering
    deploy success, auto-rollback, explicit rollback, auth, validation,
    concurrency, and server lifecycle.

- Supervision: new `SupervisionAgent` in the lifecycle package that continuously
    monitors managed services via the lifecycle backend and reacts to failures:
    bounded auto-restart with exponential backoff, escalation after N consecutive
    failures, an alert/notification callback for broker/Langfuse integration,
    and an HTTP status endpoint (`GET /status`) with per-service health summaries.
    Configurable via `ROBOTSIX_SUPERVISION_*` environment variables; degrades
    gracefully when alerting callbacks are absent. Includes 22 tests covering
    healthy steady-state, transient-failure auto-restart, escalation, lifecycle,
    and configuration.

### Changed

- Broker: extracted `_validate_register_payload` method from `_handle_register`,
    reducing the longest function in the codebase from ~186 lines to ~70 lines of
    orchestration. Validation is now a pure data-in/data-out helper.

### Added

- Add `fail_under = 85` to `[tool.coverage.report]` in `pyproject.toml` so local
    coverage runs (e.g. `pytest --cov`) also enforce the same 85% branch-coverage
    threshold that CI requires.

- Export `BUILTIN_HANDLERS` (public alias for the built-in handler kind→method mapping)
    from `responder.py` so tests can reference it instead of hardcoding kind strings.

- Broker: embedded traffic recorder (bounded, thread-safe ring buffer) and
    `GET /traffic` JSON endpoint with `agent`, `topic`, `since`,
    `until`, and `limit` query filters. `GET /agents` now includes
    `last_seen_seconds_ago`, `ttl_seconds`, `status`, and `mailbox`
    for each registered agent.

- Broker: monitoring dashboard at `GET /dashboard` (and `GET /`) serving
    a self-contained HTML page with registered-agents table, live message
    traffic table, and filter/search controls (agent, topic, time window).
    Gated by `ROBOTSIX_BROKER_DASHBOARD_ENABLED` (default off). Browser
    auth uses a `?token=` query parameter validated against the existing
    bearer-token store; the `Authorization` header remains authoritative.

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
