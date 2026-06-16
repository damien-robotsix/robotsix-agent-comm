# 6. Broker-server architecture, security model, and deployment

- Status: Accepted
- Date: 2026-06-14

## Context

The agent-comm system currently provides an in-process transport (ADR
0002) and an HTTP+JSON network transport (ADR 0005) that both
implement the abstract `Transport` interface. These transports move
messages between endpoints that already know about each other — but
there is no **standalone broker server** that lets agents in separate
processes or services register themselves, discover other agents, and
exchange messages across a network without hard-coded endpoint
coordinates.

ADR 0003 (addressing and routing) already defines the *logical* broker
role (string addresses over `Metadata.sender` / `Metadata.recipient`,
broker-mediated routing, undeliverable handling via `Error`).  ADR
0005 gives us a concrete wire protocol (HTTP + JSON) that can carry
messages between agents anywhere on a reachable network.  What is
missing is the architectural decision to combine these into a
**standalone broker daemon** — a long-running server process that
agents can address over the network to register, deregister, discover
peers, and send messages — along with the security and deployment
model that makes such a server safe to operate.

This ADR captures those architectural decisions so that every
subsequent epic child (core broker implementation, heartbeat-based TTL
eviction, client-side networked transport, TLS + authentication,
authorization / anti-spoofing / rate-limiting / audit logging, and
end-to-end integration tests) can be built against one shared,
reviewed specification.

The ADR honours ADR 0001 (stdlib-first) by not requiring any
additional runtime dependency for the core broker logic, and it
remains consistent with the addressing model of ADR 0003 (there is no
new on-wire schema; the broker simply routes over the existing
`Metadata` fields).

## Decision

### 1. Architecture — standalone daemon, HTTP+JSON transport, central registry

The broker server is a **standalone, long-running daemon** that
exposes an HTTP+JSON API built on the transport primitives from ADR
0005.  It **programs to the abstract `Transport` interface** defined in
ADR 0002 (specifically `Transport.send` and the `Registry` contract),
so the broker's internal routing logic is decoupled from the concrete
wire transport and can be tested with an in-process stub.

The broker maintains a **central, in-memory agent registry** with four
operations:

| Operation      | HTTP method / path     | Behaviour                                                   |
|----------------|------------------------|-------------------------------------------------------------|
| **Register**   | `POST /agents`         | Insert or update the `agent_id` → `Endpoint` mapping.       |
| **Deregister** | `DELETE /agents/{id}`  | Remove the mapping.  Idempotent (success if already gone).  |
| **Discovery**  | `GET /agents`          | Return the list of currently registered agent identifiers.  |
| **Send**       | `POST /messages`       | Deserialise the JSON body, validate `Metadata.recipient`,   |
|                |                        | route to the recipient's endpoint via the transport client, |
|                |                        | and return the serialised reply (or `204` for fire-and-forget). |

The registry is **in-memory** — consistent with the `Registry` class
already shipped with the HTTP transport.  Durable or persistent
backends (SQLite, Redis, etc.) are explicitly deferred to future work;
the in-memory design is sufficient for the initial standalone broker.

#### Heartbeat-based TTL eviction (design only)

The broker will support **heartbeat-based TTL eviction** to remove
agents that have not renewed their registration within a configurable
interval.  Every agent bears a `ttl_seconds` value (default 60) and
the broker maintains a `last_heartbeat` timestamp per registration.
A periodic background task (e.g. `asyncio`-driven, or a threading
`Timer`) sweeps expired entries.  Agents may call `POST /agents` at
any time — including with the same payload — to refresh their TTL
without changing other registration data.

The exact heartbeat mechanism, sweep interval, and TTL grace period
are **implementation details for child 3**; this ADR records the
design without prescribing the implementation.

### 2. Security model

Every broker deployment MUST implement the following security layers.
The ADR defines *what* is required; subsequent epic children decide
*how* (exact token format, rate-limit algorithm, log schema, etc.).

#### 2.1 Transport encryption (TLS)

All broker HTTP endpoints MUST be served over **TLS (HTTPS)**.
Plaintext HTTP is not accepted.  The broker's TLS configuration
(certificate, key, CA bundle) is loaded from a config-mountable path
(see Deployment model below).

#### 2.2 Per-agent authentication

Every request to the broker (except `GET /health`) MUST carry a
**per-agent credential**.  The credential is transmitted in an HTTP
header; the broker supports **tokens or shared secrets** and accepts
either form:

- `Authorization: Bearer <token>`
- `X-Agent-Token: <opaque-secret>`

The broker validates the credential against a provider-supplied store
(file, env variable, or pluggable backend — exact mechanism deferred
to the authentication child).  Requests with missing or invalid
credentials receive a `401 Unauthorized` JSON error response.

#### 2.3 Anti-spoofing (sender-identity verification)

After authentication, the broker **MUST verify that the authenticated
agent identity matches `Metadata.sender`** on every `POST /messages`
request.  If the authenticated principal `agent_a` attempts to send a
message where `Metadata.sender` is `agent_b`, the broker **MUST
reject** the request with a `403 Forbidden` error.  This prevents one
authenticated agent from impersonating another.

Register and deregister operations similarly MUST verify that the
authenticated agent identity matches the `agent_id` being
registered/deregistered.

#### 2.4 Per-agent rate limiting

The broker **MUST enforce a configurable per-agent rate limit**,
expressed as maximum requests per second (default: 20 rps).  Requests
exceeding the limit receive a `429 Too Many Requests` response.  The
rate-limit scope is the authenticated agent identity, not the source
IP address.  The exact algorithm (token bucket, sliding window, etc.)
and the storage for rate-limit state (in-memory, Redis-backed) are
deferred to the rate-limiting child.

#### 2.5 Maximum request body size

The broker **MUST enforce a maximum HTTP request body size** (default:
1 MiB).  Any `POST` body exceeding this limit is rejected with a `413
Content Too Large` response **before** the body is fully buffered or
deserialised.  This prevents memory-exhaustion attacks.

#### 2.6 Structured audit logging

All security-relevant events **MUST be logged as structured JSON
lines** to a dedicated audit log (separate from the application log).
Events include:

- Authentication successes and failures (agent identity, source IP,
  credential type, timestamp).
- Registration and deregistration changes (agent identity, operation,
  timestamp).
- Rate-limit hits (agent identity, current rate, limit, timestamp).
- Delivery failures (sender, recipient, error code, timestamp).

The exact JSON schema for each event type is deferred to the
authorisation/audit child.  The ADR requires that the audit log be
structured JSON and that each line be a self-contained, timestamped
JSON object — not that it conform to any particular third-party format
like CEF or syslog RFC 5424.

### 3. Deployment model

#### 3.1 Docker Compose service

The broker ships as a container image and is defined as a
`docker-compose` service with the following logical shape
(exact YAML is out of scope for this ADR):

- **Service name**: `robotsix-broker`
- **Image**: `ghcr.io/robotsix/robotsix-broker:latest`
- **Ports**: `8443` (HTTPS), mapped to host.
- **Volumes** (from the host or Docker secrets):
  - `/etc/robotsix/tls`: TLS certificate and private key.
  - `/etc/robotsix/broker/config.yaml`: broker configuration
    (rate limits, TTL defaults, log level, etc.).
  - `/etc/robotsix/broker/agents.yaml`: static agent credentials
    (when dynamic registration is disabled or allowed agents must
    be pre-provisioned).
  - `/var/log/robotsix-broker`: audit and application log output.

The broker reads its configuration from environment variables with a
`ROBOTSIX_BROKER_` prefix and/or from the mounted YAML config file.
The exact precedence and schema are deferred to the deployment child.

#### 3.2 Consumer-agent configuration

Every agent that talks to the broker **MUST** be configured with:

1. **Broker URL** — the full HTTPS base URL of the broker, e.g.
   `https://broker.example.com:8443`.  Configured via the environment
   variable `ROBOTSIX_BROKER_URL`.

2. **Auth token** — the credential the agent presents to authenticate.
   Configured via the environment variable `ROBOTSIX_BROKER_TOKEN`.

These environment variables are the **canonical** configuration
mechanism for consumer agents.  A YAML/JSON config-file fallback is
acceptable but secondary; the environment variable simplifies
containerised and twelve-factor-app deployments.

## Consequences

### What this ADR enables

- **Parallel implementation.**  Every subsequent epic child — core
  broker, heartbeat eviction, client-side transport, TLS/auth,
  authorisation/rate-limiting/audit, and integration tests — has a
  single, reviewed specification to implement against.  No child needs
  to re-negotiate architecture or security boundaries.

- **Transport-independence.**  Because the broker programs to the ADR
  0002 `Transport` interface, the core broker logic can be tested with
  the in-process transport before the networked transport is fully
  stood up, and a future transport (e.g. WebSocket, gRPC) could be
  substituted behind the same interface without touching broker logic.

- **Gradual security layering.**  A working broker can ship with
  minimal security (plain HTTP, no auth) during early development and
  integration, then add TLS, authentication, anti-spoofing, rate
  limiting, and audit logging in additive layers — each behind a
  feature flag or configuration toggle — because the ADR defines the
  *final* security posture up front.

### What this ADR constrains

- **All broker implementations must follow the security model.**  Any
  broker server — the reference implementation, a future Go/Rust port,
  or a third-party replacement — MUST implement TLS, per-agent
  authentication, anti-spoofing, per-agent rate limiting, body-size
  limits, and structured audit logging.  The SECURITY.md and
  operational runbooks will be written assuming these guarantees.

- **The HTTP+JSON wire protocol is fixed.**  Agents talk to the broker
  over the exact same message format and endpoint semantics defined in
  ADR 0005.  Any future transport must be additive behind the ADR 0002
  interface and cannot break the HTTP+JSON contract without a new ADR.

### What this ADR defers

- **Exact token format and cryptographic choices.**  The ADR requires
  per-agent authentication but does not prescribe JWT, opaque tokens,
  HMAC, or OAuth.  That decision belongs to the authentication child
  (child 5).

- **Specific rate-limit algorithm and state storage.**  The ADR
  requires per-agent rate limiting but leaves the algorithm (token
  bucket, sliding window, leaky bucket) and the backing store
  (in-memory dict, Redis Sorted Sets) to the rate-limiting child.

- **Exact audit log JSON schema.**  The ADR requires structured JSON
  audit logging and names the event categories, but the precise field
  names, nested structure, and serialisation format are decided in the
  authorisation/audit child.

- **Persistent registry storage.**  The ADR describes an in-memory
  registry; durable persistence (SQLite, Redis, etcd) is future work
  and will require its own ADR when needed.

- **Dynamic vs. static agent provisioning.**  The ADR defines the
  credential header and validation checkpoint but does not decide
  whether agents are pre-provisioned in a static file, registered
  dynamically with a bootstrap token, or managed via an external
  identity provider.  That is deferred to the authentication child.

- **NAT traversal, firewall hole-punching, or TURN/relay.**  As in ADR
  0005, these are out of scope.  The broker assumes directly reachable
  HTTP endpoints; a relay/gateway can be added later without changing
  this architecture.
