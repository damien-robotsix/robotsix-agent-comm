<!-- vale on -->

# Deploying the lifecycle server

The `robotsix-agent-comm.lifecycle` subsystem is the **central deployment
and lifecycle management component** for the robotsix ecosystem.  It
provides a brokered responder (`LifecycleServer`) that registers on the
agent-comm broker and exposes status/lifecycle handlers, together with a
supervision agent (`SupervisionAgent`) that continuously monitors managed
services, auto-restarts them with exponential backoff, and escalates after
repeated failures.

See [the lifecycle API reference](../api/index.md#robotsix_agent_comm.lifecycle)
for the auto-generated class and function documentation.

______________________________________________________________________

## Architecture overview

The lifecycle subsystem is composed of four layers:

| Component            | Role                                                   |
| -------------------- | ------------------------------------------------------ |
| `LifecycleConfig`    | Immutable configuration parsed from `ROBOTSIX_LIFECYCLE_*` env vars — broker identity, TLS, and Langfuse credentials. |
| `LifecycleServer`    | A `BrokeredResponder` that registers on the agent-comm broker and serves `monitor`, `status`, and `lifecycle` request handlers with optional Langfuse tracing. |
| `SupervisionAgent`   | A background loop that health-checks every managed service, tracks per-service state, attempts bounded restarts with exponential backoff, and escalates when the restart threshold is exceeded. |
| `LifecycleBackend`   | Pluggable interface for start/stop/health operations. Two implementations ship: `SubprocessBackend` (Docker Compose) and `MockBackend` (test double). |

These layers are wired together by `build_server()` (in `service.py`) and
`build_supervisor()`.  The `python -m robotsix_agent_comm.lifecycle`
entrypoint reads environment variables, constructs the config and server,
and blocks until signalled.

### Relationship with the broker

The lifecycle server is an **agent** from the broker's perspective.  It
authenticates with a bearer token (configured via
`ROBOTSIX_LIFECYCLE_BROKER_TOKEN`), registers its agent identity, and
exposes three request kinds: `monitor` (built-in health), `status`
(custom status), and `lifecycle` (accepts commands such as restarting a
named service).  The broker itself is documented in [the broker deployment
guide](../broker/deployment.md) and its architecture in
[ADR 0006](../decisions/0006-broker-server-architecture.md).

______________________________________________________________________

## Configuration reference

Every lifecycle variable uses the `ROBOTSIX_LIFECYCLE_` prefix; every
supervision variable uses the `ROBOTSIX_SUPERVISION_` prefix.

### Lifecycle server

| Variable | Meaning | Default |
|---|---|---|
| `ROBOTSIX_LIFECYCLE_AGENT_ID` | Agent identity on the broker | `lifecycle-server` |
| `ROBOTSIX_LIFECYCLE_BROKER_HOST` | Broker host | `localhost` |
| `ROBOTSIX_LIFECYCLE_BROKER_PORT` | Broker port | `8443` |
| `ROBOTSIX_LIFECYCLE_BROKER_SCHEME` | Broker scheme (`http` or `https`) | `https` |
| `ROBOTSIX_LIFECYCLE_BROKER_TOKEN` | Broker bearer token | *(unset)* |
| `ROBOTSIX_LIFECYCLE_BROKER_TLS_CA` | Broker TLS CA bundle path | *(unset)* |
| `ROBOTSIX_LIFECYCLE_LANGFUSE_PUBLIC_KEY` | Langfuse public key | *(unset)* |
| `ROBOTSIX_LIFECYCLE_LANGFUSE_SECRET_KEY` | Langfuse secret key | *(unset)* |
| `ROBOTSIX_LIFECYCLE_LANGFUSE_HOST` | Langfuse host URL | *(unset)* |

A missing `ROBOTSIX_LIFECYCLE_BROKER_TOKEN` emits a **warning** at startup
(the broker may have authentication disabled in development), but a
production deployment should always set it.

### Supervision agent

| Variable | Meaning | Default |
|---|---|---|
| `ROBOTSIX_SUPERVISION_POLL_INTERVAL_SECONDS` | Seconds between full poll cycles | `30.0` |
| `ROBOTSIX_SUPERVISION_HEALTH_TIMEOUT_SECONDS` | Per-service health-check timeout | `10.0` |
| `ROBOTSIX_SUPERVISION_MAX_RESTART_ATTEMPTS` | Max restarts before escalation | `3` |
| `ROBOTSIX_SUPERVISION_BACKOFF_BASE_SECONDS` | Exponential backoff base delay | `5.0` |
| `ROBOTSIX_SUPERVISION_BACKOFF_MAX_SECONDS` | Maximum backoff cap | `300.0` |
| `ROBOTSIX_SUPERVISION_ESCALATION_COOLDOWN_SECONDS` | Cooldown after escalation | `600.0` |
| `ROBOTSIX_SUPERVISION_SERVICES` | Comma/space-separated service names | *(unset)* |
| `ROBOTSIX_SUPERVISION_STATUS_HOST` | Status HTTP endpoint bind host | `127.0.0.1` |
| `ROBOTSIX_SUPERVISION_STATUS_PORT` | Status HTTP endpoint bind port (`0` = OS-assigned) | `0` |

The `SupervisionConfig.validate()` method (called automatically by
`SupervisionConfig.from_env()`) enforces these constraints and raises
`ValueError` on invalid values.

______________________________________________________________________

## Quickstart

Set the required environment variables and run:

```bash
# Set required env vars
ROBOTSIX_LIFECYCLE_BROKER_HOST=localhost \
ROBOTSIX_LIFECYCLE_BROKER_PORT=8443 \
ROBOTSIX_LIFECYCLE_BROKER_TOKEN=my-token \
ROBOTSIX_SUPERVISION_SERVICES=agent-a,agent-b \
python -m robotsix_agent_comm.lifecycle
```

The lifecycle server will register on the broker, start the supervision
poll loop, and begin serving the status HTTP endpoint on an OS-assigned
port.

______________________________________________________________________

## Supervision agent: health checks, restart, and escalation policy

The `SupervisionAgent` runs a background poll loop that inspects every
service listed in `ROBOTSIX_SUPERVISION_SERVICES` once per
`poll_interval_seconds`.  Each service follows a three-step failure
policy:

### 1. Degraded

The first health-check failure records a `DEGRADED` incident with a
human-readable message.  The agent attempts a restart immediately.

### 2. Restart with exponential backoff

The agent calls `backend.stop()` followed by `backend.start()`.  After
each restart the backoff delay doubles, capped at `backoff_max_seconds`:

```
delay = min(base × 2^(attempt − 1), max_backoff)
```

where *base* is `ROBOTSIX_SUPERVISION_BACKOFF_BASE_SECONDS`, *attempt* is
the 1-based restart count for the current failure streak, and
*max_backoff* is `ROBOTSIX_SUPERVISION_BACKOFF_MAX_SECONDS`.  The
defaults (base 5 s, cap 300 s) produce the sequence **5 s → 10 s → 20 s
→ … → 300 s**.

A `RESTARTED` incident is recorded after every restart attempt
regardless of whether `start()` succeeded.

### 3. Escalation

After `max_restart_attempts` consecutive restart attempts have all
failed, the agent records an `ESCALATED` incident and stops
auto-restarting.  Polling for that service resumes after
`escalation_cooldown_seconds` (default 10 minutes).  If the service
recovers (health check passes), the escalation state is cleared
automatically and all counters reset.

### Incident callback

Every incident is forwarded to an optional `on_alert` callback
(`AlertHandler`).  This is the integration point for wiring broker
notifications, Langfuse traces, or external alerting systems.

______________________________________________________________________

## Status HTTP endpoint

The supervision agent binds a lightweight HTTP server (on
`ROBOTSIX_SUPERVISION_STATUS_HOST`:`ROBOTSIX_SUPERVISION_STATUS_PORT`)
that serves `GET /status`.

### Response shape

```json
{
  "services": {
    "agent-a": {
      "healthy": true,
      "consecutive_failures": 0,
      "restart_count": 0,
      "escalated": false,
      "last_health_check": 1719600000.0,
      "last_failure": null,
      "last_restart": null,
      "escalation_time": null,
      "recent_incidents": []
    }
  },
  "running": true,
  "poll_count": 42
}
```

| Field | Type | Meaning |
|---|---|---|
| `services.<name>.healthy` | `bool` | Whether the latest health check passed |
| `services.<name>.consecutive_failures` | `int` | Consecutive failed health checks |
| `services.<name>.restart_count` | `int` | Total restart attempts for this service |
| `services.<name>.escalated` | `bool` | Whether the service is currently escalated |
| `services.<name>.recent_incidents` | `list` | Last 10 incidents (timestamp, kind, message, attempt) |
| `running` | `bool` | Whether the supervision loop is active |
| `poll_count` | `int` | Number of completed poll cycles |

______________________________________________________________________

## Backends

### SubprocessBackend

The production backend shells out to `docker compose` for start, stop, and
health operations:

- `start()` runs `docker compose up -d <service>`.  An optional `version`
  parameter sets the `SERVICE_VERSION` environment variable so the Compose
  file can reference it (`image: my-svc:${SERVICE_VERSION:-latest}`).
- `stop()` runs `docker compose stop <service>`.
- `health()` checks `docker compose ps --status running <service>` and
  returns `True` when the service name appears in the output.

The working directory defaults to the current directory; pass
`project_dir` to point at a specific `docker-compose.yml`.

### MockBackend

A test double that records all method calls and returns canned health
results.  Construct it with a list of booleans — each `health()` call
consumes the next value; once the list is exhausted the *last* value is
repeated.

```python
from robotsix_agent_comm.lifecycle import MockBackend, SupervisionConfig, build_supervisor

backend = MockBackend(health_results=[True, True, False])
config = SupervisionConfig(services=("demo-svc",), max_restart_attempts=2)
supervisor = build_supervisor(config, backend=backend)
```

______________________________________________________________________

## Alert callbacks and Langfuse tracing

### AlertHandler

```python
from collections.abc import Callable
from robotsix_agent_comm.lifecycle import Incident

AlertHandler = Callable[[Incident], None]
```

Pass an `on_alert` callback to `build_supervisor()` to receive every
incident the supervisor emits.  This is the hook for wiring broker
notifications, logging, or external alerting.

### LifecycleTracing

`LifecycleTracing` wraps the Langfuse Python SDK and operates in **no-op
mode** when the SDK is not installed or when credentials
(`ROBOTSIX_LIFECYCLE_LANGFUSE_PUBLIC_KEY` /
`ROBOTSIX_LIFECYCLE_LANGFUSE_SECRET_KEY`) are missing.  This means the
lifecycle server **gracefully degrades** — tracing is transparent
instrumentation, never a hard dependency.

When enabled, every lifecycle-server request handler (`monitor`,
`status`, `lifecycle`) is automatically wrapped in a Langfuse trace span.

______________________________________________________________________

## Deployment considerations

### Production

- **Always set `ROBOTSIX_LIFECYCLE_BROKER_TOKEN`.**  The config validator
  emits a warning when it is missing, but production deployments must
  authenticate.
- Use `ROBOTSIX_LIFECYCLE_BROKER_TLS_CA` when connecting to a broker that
  uses a custom CA (e.g. an internal PKI or self-signed certificate).
- Set `ROBOTSIX_LIFECYCLE_BROKER_SCHEME=https` (the default) unless the
  broker is deliberately running without TLS.

### Docker

The lifecycle server runs inside the same containerised environment as the
services it supervises.  The `SubprocessBackend` requires the `docker`
binary and access to the Docker socket.

### Relationship with broker auth

The lifecycle server authenticates to the broker with a bearer token,
exactly like any other agent.  The broker's `agent-tokens.json` must
include an entry for the lifecycle server's `agent_id` (default
`lifecycle-server`).

______________________________________________________________________

## Design decisions

See [ADR 0006 — Broker-server architecture, security model, and
deployment](../decisions/0006-broker-server-architecture.md) for the
broker-side architecture that the lifecycle server relies on.

The lifecycle server's public API is documented in the [API
reference](../api/index.md#robotsix_agent_comm.lifecycle).
