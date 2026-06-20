# Transport — HTTP+JSON network implementation

This note describes the **network transport** added in Phase 5
(`src/robotsix_agent_comm/transport/`). It is one concrete implementation
of the abstract transport interface from
[ADR 0002](../decisions/0002-transport-architecture.md), built on the
standard library per [ADR 0001](../decisions/0001-stdlib-first.md) and
recorded in [ADR 0005](../decisions/0005-http-json-transport.md). It is
consistent with the addressing model of
[ADR 0003](../decisions/0003-addressing-and-routing.md).

## Abstract interface

`Transport` (in `transport/base.py`) is the abstraction ADR 0002 calls
for. Its job is narrow: move serialized `Message` envelopes between
endpoints. It does not resolve addresses (the router's job) or interpret
bodies. Both this HTTP transport and the in-process baseline implement it,
so the router and client API are written once against the abstraction.

```python
class Transport(ABC):
    def send(self, message, endpoint, *, timeout) -> Message | None: ...
    def health_check(self, endpoint, *, timeout) -> bool: ...
```

## Components

- **`Endpoint`** — a `kw_only` dataclass describing where an agent listens:
    `agent_id`, `host`, `port`, `scheme` (default `http`), and `path`
    (default `/messages`). Its `url` / `health_url` properties keep
    endpoints URL-shaped.
- **`Registry`** — a thread-safe, in-memory map of `agent_id` to
    `Endpoint` for registration and discovery (`register`, `unregister`,
    `lookup`, `list_agents`). Unknown agents raise `AgentNotFoundError`.
- **`TransportServer`** — wraps `http.server.ThreadingHTTPServer`. `POST`
    to the message path deserializes the body, dispatches to a user-supplied
    `handler(message) -> Message | None`, and returns the serialized reply
    (`204` for notifications). `GET /health` returns `200` with a small JSON
    status body. Binds `port=0` for an ephemeral test port and exposes the
    actual bound `port`.
- **`TransportClient`** — uses `http.client` with per-request timeouts.
    `send` serializes a message, POSTs it, and deserializes any reply;
    `health_check` GETs `/health`. Socket/HTTP/timeout failures surface as
    `TransportError` subclasses (`TransportTimeoutError` for timeouts).
- **`Router`** — ties registry + client + retry policy together. `route`
    reads `Metadata.recipient`, looks the endpoint up in the registry, and
    delivers via the client with retry + timeout.

## Retry and timeout model

ADR 0004 fixes the *baseline* in-process transport as no-retry. A network
transport instead tolerates transient failures, so this layer adds a
`RetryPolicy` (`max_attempts`, `base_delay`, `backoff_factor`, `max_delay`)
and a `retry_call` helper that backs off exponentially —
`min(base_delay * backoff_factor ** (attempt - 1), max_delay)` — between
attempts. The sleep function is injectable so tests run without real
delays. Exhausted retries raise `DeliveryError`, carrying the last
underlying cause. Every client call takes a per-request `timeout`. This is
layered *behind the same interface* (ADR 0002) and does not change message
semantics.

## NAT / firewall limitation (out of scope)

This transport assumes each agent runs a **directly reachable HTTP
listener** registered as a URL. NAT traversal, firewall hole-punching, and
TURN/relay tunneling are **out of scope** for this phase. Endpoints are
kept URL-shaped precisely so a future relay/gateway can sit in front of the
registry and rewrite/forward addresses without changing this layer.

There is also **no authentication, TLS, or encryption** in this phase
(plain HTTP); the registry is in-memory only (no persistent or networked
discovery backend). Both are noted as future work.
