## 0.0.0 (unreleased)

- Enable `changelog_autofill` periodic workflow to auto-propose changelog
  entries from merged PRs.
- Sync docs and template to hatch-vcs versioning — replace static
    `[project].version` references with `dynamic = ["version"]` +
    `[tool.hatch.version] source = "vcs"`; remove `__version__` from the
    template package init; remove the stale tag/version guard from the
    template `publish.yml`

- Add `.editorconfig` at repository root for consistent editor indentation and whitespace handling across contributors.

- Remove stale LLM and Chat SSE variables from `.env.example` and `AGENT.md`; keep only the `LOG_LEVEL` variable that is actually consumed by the code.

- Wire `deptry` into dev dependencies, `Makefile`, pre-commit hooks, and the audit CI workflow.

- Extracted shared `_parse_bool` and `make_env_getter` config helpers into `protocol._config_helpers`, eliminating byte-for-byte duplication across `broker/config.py`, `lifecycle/config.py`, and `lifecycle/supervision.py`.

- Extract shared `_do_post()` and `_check_health()` HTTP helpers in `transport/_http.py`, eliminating duplicate request/error-handling boilerplate across `client.py` and `brokered.py`.

- Bump pre-commit hooks: `pre-commit-hooks` from v5.0.0 to v6.0.0, `ruff-pre-commit` from v0.15.15 to v0.15.20

# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `BrokerConfig.max_body_size` now defaults to `1_048_576` (1 MiB) instead of
    `None`, resolving a mismatch where the code had no default but the
    documentation stated "server default (1 MiB)".

### Added

- Dependabot `pre-commit` package-ecosystem: automatic grouped PRs for pinned
    pre-commit hook updates on a weekly schedule.

- `.robotsix-mill/periodic/env_doc_sync.yaml`: enable the `env_doc_sync` periodic
    workflow that cross-references discovered environment variables against
    `docs/configuration.md` and files tickets for documentation gaps.

- Document `ROBOTSIX_BROKER_MAILBOX_GRACE_SECONDS` in the Tuning section of
    `docs/broker/deployment.md`.

- Document `ROBOTSIX_BROKER_DASHBOARD_ENABLED` in `docs/broker/deployment.md`
    Tuning table (was already in `.env.broker.example` but missing from the
    formal reference doc).

- `docs/lifecycle/index.md` — comprehensive deployment guide for the lifecycle
    subsystem covering architecture, configuration (18 env vars), quickstart,
    supervision policy (health checks, exponential backoff, escalation), status
    HTTP endpoint, backends, Langfuse tracing, and deployment considerations.

- `IncidentKind` `StrEnum` (`DEGRADED`, `RESTARTED`, `ESCALATED`) replacing raw string
    literals for `Incident.kind`.

- `TrafficDisposition(StrEnum)` and `AgentStatus(StrEnum)` in
    `robotsix_agent_comm.protocol._types` — single source of truth for
    traffic disposition values ("queued", "rejected", "routed",
    "unknown_recipient") and agent liveness statuses ("active", "stale",
    "unknown"). Replaces raw string literals in `broker/server.py` and
    `broker/_dashboard.py`; the `UNKNOWN_RECIPIENT` constant in
    `transport/errors.py` is now aliased from the enum.

- Dashboard CSS badges (`badge-routed`, `badge-unknown-recipient`) and JS
    handling for the "routed" and "unknown_recipient" traffic dispositions,
    closing a gap where they fell through to the default `badge-unknown` style.

- Expanded `Makefile` with `help`, `check-all`, `clean`, `coverage-report`,
    `typecheck`, `security`, `docs-serve`, `docs-build`, and `docker-build`
    targets, modelled on Litestar's dev workflow pattern.

### Fixed

- Correct `ROBOTSIX_BROKER_RATE_LIMIT` default in `docs/broker/deployment.md`
    from "server default (0)" to "unset (off)", matching the actual code
    behaviour where the config field is `None` when the env var is unset.

- `LifecycleServer` no longer advertises `config-get` and `config-set` in
    its `supported_kinds`, matching its actual capability set (only `monitor`,
    `status`, and `lifecycle` are handled).

### Changed

- Adopt hatch-vcs for single-source versioning: remove static `version = "0.1.0"`
    from `pyproject.toml`, remove `__version__` from
    `src/robotsix_agent_comm/__init__.py`, and derive the package version from
    Git tags at build time. Consumers should use
    `importlib.metadata.version("robotsix-agent-comm")` instead of the removed
    `__version__` attribute. Simplify `publish.yml`: remove the `verify-tag` job
    (redundant — hatch-vcs bakes the tag into the build); add `fetch-depth: 0`
    to checkout steps so hatch-vcs can resolve tags.

- Upgrade `astral-sh/setup-uv` from v5.4.2 to v8.2.0 with `enable-cache: true`
    across all CI workflows (`.github/workflows/ci.yml`, `audit.yml`,
    `publish.yml`) and their Python-package templates; eliminates repeated
    re-downloads of uv-managed tools on every CI run.

- Replace abandoned `detect-secrets` pre-commit hook with actively-maintained
    `gitleaks` v8.30.1 for secrets scanning; add `.gitleaks.toml` with
    path-based allowlists for test/fixture directories; remove
    `.secrets.baseline`; document secrets scanning policy in `SECURITY.md`.

- Migrate `templates/python-package/` scaffold from detect-secrets to gitleaks:
    replace hook in `.pre-commit-config.yaml`, add starter `.gitleaks.toml`,
    remove stale `.secrets.baseline`.

- `ROBOTSIX_BROKER_TTL_SECONDS` now defaults to 60 in `BrokerConfig`
    (was `None` at the config layer, relying on the server constructor default).
    The effective default is unchanged (60); the config is now self-documenting.

### Added

- Docstrings for all 6 HTTP handler methods (`do_GET`, `do_POST`, `do_DELETE`)
    across `_BrokerRequestHandler`, `_StatusRequestHandler`, and
    `_MessageRequestHandler`.

- `#:` docstring comments for `DEFAULT_RETRY_POLICY` in `broker/server.py` and
    `REQUIRED_ENVELOPE_FIELDS` in `protocol/validation.py`, closing the last
    two undocumented module-level public constants.

- `robotsix_agent_comm._logging.setup_logging()`: shared logging configuration
    for CLI entry points. Reads `LOG_LEVEL` from the environment (default
    `INFO`) and configures the root logger with ISO-8601 timestamps on stdout.
    Called as the first statement in both `broker/service.py:main()` and
    `lifecycle/service.py:main()`.

- `robotsix_agent_comm.protocol.config_contract`: shared base types for
    broker `config-get` / `config-set` request kinds — `ConfigContractError`,
    `ConfigContract` Protocol, `SecretRedactor`, and `SettableKey`.

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

- Broker: extracted `_build_agent_entry` helper method from `/agents` handler,
    reducing nesting depth from 6 to 3 and making per-agent entry construction
    a pure, side-effect-free function.

- Broker: extracted `_validate_register_payload` method from `_handle_register`,
    reducing the longest function in the codebase from ~186 lines to ~70 lines of
    orchestration. Validation is now a pure data-in/data-out helper.

- Extracted duplicated `_write_json` method into a shared `_BaseRequestHandler`
    mixin class in `transport/server.py`, eliminating triplication across
    `_MessageRequestHandler`, `_BrokerRequestHandler`, and
    `_StatusRequestHandler`.

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

- Lifecycle: extracted `_build_status_result` helper from `handle_monitor`
    and `handle_status`, eliminating 9 lines of duplicated result-dict
    construction and optional tracing logic.

- Extracted shared `_run_until_signalled(server, logger)` helper in
    `robotsix_agent_comm.service_utils` to eliminate duplicated
    signal-handling/shutdown code in both `broker.service.main` and
    `lifecycle.service.main`.

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
