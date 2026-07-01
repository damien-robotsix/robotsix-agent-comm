# robotsix-agent-comm

Agent communication stack for the robotsix ecosystem — a Python library
providing a typed message protocol, an HTTP+JSON network transport, a
high-level agent SDK, and a broker server (`robotsix-broker`).

**This repo is not an agent itself.** It ships the SDK and broker that other
repos use to build and connect agents. There is no `agent_definitions/`
directory, no Langfuse integration, and no persistent data directory.

## Key directories

| Directory                            | Purpose                                                                           |
| ------------------------------------ | --------------------------------------------------------------------------------- |
| `src/robotsix_agent_comm/sdk/`       | Consumer API — the `Agent` class developers use to build agents                   |
| `src/robotsix_agent_comm/broker/`    | Broker server — authenticates agents and relays messages (CLI: `robotsix-broker`) |
| `src/robotsix_agent_comm/protocol/`  | Typed message schemas (`Request`, `Response`, `Message`)                          |
| `src/robotsix_agent_comm/transport/` | Low-level HTTP+JSON transport and agent registry                                  |
| `docs/`                              | Documentation site (MkDocs), including ADRs under `docs/decisions/`               |
| `examples/`                          | Runnable examples (e.g. `request_response.py`)                                    |
| `templates/`                         | Jinja2 templates (broker)                                                         |
| `tests/`                             | Pytest suite                                                                      |

## Build, test, lint

This is a uv-based Python project (requires Python ≥3.12, zero mandatory
runtime dependencies).

```bash
uv sync                # install dev dependencies
uv run pytest          # run tests
uv run ruff check .    # lint
uv run ruff format .   # format
uv run mypy .          # type-check (strict mode)
```

Convenience targets via `make`: `install`, `test`, `lint`, `format`.

CI (`.github/workflows/ci.yml`) runs a matrix of Python 3.12–3.14, plus
vulture dead-code scan, mdformat, lockfile consistency check, and a
trufflehog secrets scan.

## Environment variables

### General (`.env.example`)

The only general-purpose environment variable is `LOG_LEVEL` (default
`INFO`), which controls the Python logging level for the library.  All
other configuration is sub-system-specific; see the broker and lifecycle
example files below.

### Broker (`.env.broker.example`)

All prefixed `ROBOTSIX_BROKER_` (per ADR 0006 §3.1).

| Variable                              | Default                                  | Purpose                                        |
| ------------------------------------- | ---------------------------------------- | ---------------------------------------------- |
| `ROBOTSIX_BROKER_HOST`                | `0.0.0.0`                                | Broker listen address                          |
| `ROBOTSIX_BROKER_PORT`                | `8443`                                   | Broker listen port                             |
| `ROBOTSIX_BROKER_ENV`                 | `production`                             | Deployment environment                         |
| `ROBOTSIX_BROKER_TLS_CERT`            | `/etc/robotsix/tls/server.pem`           | TLS certificate path                           |
| `ROBOTSIX_BROKER_TLS_KEY`             | `/etc/robotsix/tls/server.key`           | TLS private key path                           |
| `ROBOTSIX_BROKER_TLS_CA`              | `/etc/robotsix/tls/ca.pem`               | TLS CA bundle (mTLS)                           |
| `ROBOTSIX_BROKER_REQUIRE_CLIENT_CERT` | `false`                                  | Enable mutual TLS                              |
| `ROBOTSIX_BROKER_AGENT_TOKENS_FILE`   | `/etc/robotsix/broker/agent-tokens.json` | Agent token store (file)                       |
| `ROBOTSIX_BROKER_AGENT_TOKENS`        | —                                        | Agent token store (inline JSON, fallback)      |
| `ROBOTSIX_BROKER_TTL_SECONDS`         | `60`                                     | Agent registration TTL                         |
| `ROBOTSIX_BROKER_RATE_LIMIT`          | `0` (off)                                | Message rate limit per agent                   |
| `ROBOTSIX_BROKER_MAX_BODY_SIZE`       | `1_048_576` (1 MiB)                      | Max request body size                          |
| `ROBOTSIX_BROKER_AUDIT_LOG`           | stdout                                   | Audit log destination (path or `-` for stdout) |

## Periodic workflows

### GitHub Actions (weekly, Monday 06:00 UTC)

| Workflow      | File            | Purpose                              |
| ------------- | --------------- | ------------------------------------ |
| **Audit**     | `audit.yml`     | `pip-audit` CVE scan on dependencies |
| **CodeQL**    | `codeql.yml`    | GitHub CodeQL SAST analysis          |
| **Deps Bump** | `deps-bump.yml` | Automated dependency bump PRs        |

### robotsix-mill periodic configs (9 enabled)

Each file in `.robotsix-mill/periodic/` enables a built-in periodic workflow
for this repo:

`audit`, `bc_check`, `board_cleanup`, `completeness_check`, `copy_paste`,
`health`, `module_curator`, `survey`, `test_gap`

## Notable omissions

- **No Langfuse** — the broker is a generic message router; observability
    is left to the agents that connect to it.
- **No `agent_check`** — agents register dynamically via `POST /agents`;
    there are no hardcoded agents to check.
- **No persistent data directory** — the broker keeps no persistent state
    beyond an optional JSON-lines audit log.
- **No `agent_definitions/`** — this repo is not an agent.

## Docker

```bash
docker compose up --build   # broker + chat server
```

The `Dockerfile` is a multi-stage Python build. Images are published to
GHCR via `docker-publish.yml` on pushes to `main` and version tags.
