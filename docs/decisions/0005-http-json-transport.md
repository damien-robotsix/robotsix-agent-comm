# 5. HTTP+JSON network transport

- Status: Accepted
- Date: 2026-06-14

## Context

ADR 0002 defines an abstract transport interface with a stdlib in-process
transport as its first implementation, and states explicitly that "future
transports (e.g. inter-process or network) implement the same interface."
The agent communication system must support **distributed agents across
networks**, so a network transport is required as an additive
implementation behind that same interface.

The protocol layer (`src/robotsix_agent_comm/protocol/`) already emits and
parses messages as JSON strings via `serialize`/`deserialize`. ADR 0001
(stdlib-first) constrains us to the standard library unless a third-party
dependency is genuinely unavoidable.

## Decision

Implement the network transport as **HTTP + JSON over the Python standard
library**, behind the abstract transport interface from ADR 0002.

- **Server**: `http.server.ThreadingHTTPServer` with a
    `BaseHTTPRequestHandler` subclass. `POST` to the message path
    deserializes the body, dispatches to a user callback, and returns the
    serialized reply (`204` for fire-and-forget). `GET /health` reports
    liveness. Malformed bodies yield a `4xx` JSON error, never a `500`.
- **Client**: `http.client` with per-request timeouts. Socket/HTTP
    failures surface as `TransportError` subclasses.
- **Message bodies** are the JSON strings already produced by the protocol
    layer's `serialize`/`deserialize` — no new codec, no schema change.
- **No new runtime dependency**: `[project.dependencies]` stays empty.

### Why not gRPC / WebSocket / aiohttp?

- `grpcio` and `protobuf` add heavyweight binary dependencies and a second
    serialization format, duplicating the JSON envelope the protocol layer
    already defines — rejected under ADR 0001.
- `websockets` / `aiohttp` add a third-party async stack for a
    request/response exchange the stdlib already serves with `http.server` /
    `http.client` — rejected under ADR 0001.

The standard library covers request/response HTTP fully, so stdlib-first
wins.

## Consequences

- The transport is one concrete implementation of the ADR 0002 interface;
    the broker/router and client API still program to the abstraction.
- Because the baseline (ADR 0004) is no-retry, this transport layers a
    bounded exponential-backoff `RetryPolicy` and per-request timeouts on top
    of the wire, without changing the protocol or message semantics.
- Endpoints are URL-shaped, so a relay/gateway can later address
    NAT/firewall traversal (out of scope now; see
    `../transport/design.md`).
- No authentication, TLS, or encryption is provided in this phase (plain
    HTTP); that is future work and would not change the envelope.
- Adopting a richer transport (durable, TLS, relayed) remains an additive
    change behind the same interface and would reference and update ADR 0001
    if it needed a dependency.
