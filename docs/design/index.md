# Design

This document describes the architecture of `robotsix-agent-comm`: how
agents address one another and how messages travel between them. It is
grounded in the protocol layer that already exists today and lays out
the contracts that the later transport, broker/router, and client-API
layers implement to.

## System Architecture

### Protocol foundation (exists today)

Communication is built on the message envelope defined in
`src/robotsix_agent_comm/protocol/`. Every message is a `Message`
dataclass carrying:

- `type` — a `MessageType` discriminator, one of `REQUEST`,
  `RESPONSE`, `ERROR`, or `NOTIFICATION`.
- `metadata` — a `Metadata` value whose `sender` (str) and
  `recipient` (str | None) fields carry the addressing information,
  plus a `timestamp` and an `extra: dict[str, Any]` escape hatch for
  application-defined metadata.
- `message_id` — a unique identifier minted per message.
- `correlation_id` — links a reply back to the message it answers
  (None for requests and notifications).
- `body` — an application-defined `dict[str, Any]` payload.
- `protocol_version` — the wire-protocol version, currently
  `PROTOCOL_VERSION = "1.0"`.

The four concrete kinds subclass `Message`:

- `Request` — expects a matching `Response`; its `correlation_id` is
  always None.
- `Response` — answers a `Request`; requires a `correlation_id`.
- `Error` — reports a failure, optionally correlated to a failing
  request.
- `Notification` — fire-and-forget, with no correlation.

The request↔response correlation mechanism is the classmethods
`Response.to(request)` and `Error.to(request)`. Both swap routing —
the reply's `sender` becomes the request's `recipient` and its
`recipient` becomes the request's `sender` — and set
`correlation_id = request.message_id`. `Error.to(...)` additionally
fills `body` via `error_body(code, message, **details)`, producing a
structured `{"code": ..., "message": ..., ...}` payload.

### Layering

The system is layered to minimize coupling. Each layer depends only on
the one beneath it:

```
+-----------------------------------------------------------+
|  Client API            (later epic child — not yet built) |
|  public, ergonomic surface: send a Request and await the  |
|  correlated Response; publish Notifications; register      |
|  handlers.                                                 |
+-----------------------------------------------------------+
|  Broker / Router       (later epic child — not yet built) |
|  resolves Metadata.recipient to a delivery destination;   |
|  emits Error on undeliverable messages.                   |
+-----------------------------------------------------------+
|  Transport             (later epic child — not yet built) |
|  abstract interface + stdlib in-process implementation    |
|  (asyncio / queue) that moves serialized Messages.        |
+-----------------------------------------------------------+
|  Protocol              (EXISTS TODAY)                     |
|  Message/Request/Response/Error/Notification, Metadata,   |
|  MessageType, JSON serialize/deserialize, validate.       |
+-----------------------------------------------------------+
```

Only the **Protocol** layer exists today. **Transport**,
**Broker/Router**, and **Client API** land as later children of this
epic, each implementing to the contracts described below.

### Data flow

**Request → Response round-trip.** A caller builds a `Request` with
`Metadata.sender = "agent-a"` and `Metadata.recipient = "agent-b"`,
hands it to the client API, which serializes it and passes it to the
transport. The broker reads `recipient`, resolves `"agent-b"` to its
registered endpoint, and delivers the message. `agent-b` processes the
request and replies with `Response.to(request, body=...)`; the swapped
routing addresses the reply back to `"agent-a"`, and the matching
`correlation_id` lets the client API resolve the awaiting caller.

**Fire-and-forget `Notification`.** A caller builds a `Notification`
with a `recipient` and hands it to the client API. The broker resolves
and delivers it exactly as a request, but no reply or correlation is
expected; if the recipient cannot be resolved the broker raises the
undeliverable behaviour described below.

## Addressing and Routing

### Address scheme

An agent address is the plain string stored in `Metadata.sender` and
`Metadata.recipient`. **No new schema field is introduced** — the
existing string fields are the addressing primitive. A valid agent
address is a non-empty, case-sensitive string naming a single logical
endpoint (for example `"agent-a"`, `"planner"`, or a namespaced form
such as `"team/worker-3"`). A message is targeted at an endpoint by
setting `Metadata.recipient` to that endpoint's address; `sender`
identifies the originator so replies can be routed back via
`Response.to` / `Error.to`. Application-defined routing hints (for
example a partition key) may travel in `Metadata.extra` without any
breaking protocol change.

### Broker-mediated routing

The broker/router (a later epic child) maintains a registry mapping
each address to a delivery destination (a transport endpoint / queue).
To route a message it reads `Metadata.recipient`, looks the address up
in the registry, and forwards the message over the transport to the
matching destination. Endpoints register and deregister their address
with the broker as they join and leave.

### Undeliverable messages

A message is **undeliverable** when `Metadata.recipient` is missing
(None or empty) or names an address with no registered endpoint. The
documented mechanism is the protocol's existing `Error` message: the
broker replies to the originator with
`Error.to(message, code=..., message=...)`, which swaps the routing
back to the original `sender` and sets `correlation_id` to the
undeliverable message's `message_id`. The structured body is built
with `error_body(code, message, ...)`.

The canonical error `code` strings the broker emits are:

- `"unknown_recipient"` — `recipient` named an address that is not
  registered.
- `"missing_recipient"` — `recipient` was None or empty.

A `Notification` that is itself undeliverable is reported with the
same `Error` mechanism back to its `sender`; an undeliverable `Error`
(no live originator) is dropped to avoid an error loop.

## Delivery Semantics

These guarantees describe the **baseline in-process transport** (the
stdlib `asyncio` / `queue` implementation that is the first transport
child). Stronger guarantees can be layered on later transports without
changing the protocol.

- **Delivery guarantee: at-most-once.** A message handed to the
  in-process transport is delivered to its endpoint zero or one time;
  there is no acknowledgement or redelivery. Justification: this is
  the guarantee a stdlib in-memory `queue.Queue` / `asyncio.Queue`
  provides directly (ADR 0001), with no broker-side persistence or
  dedup machinery to build or maintain.
- **Ordering guarantee: per-endpoint FIFO.** Messages destined for a
  single endpoint are delivered in the order the broker enqueued them.
  There is no global ordering across endpoints. Justification: a
  per-endpoint stdlib queue preserves enqueue order for free, while
  global ordering would require a serialization point that the
  stdlib-first, in-process design does not need.
- **Retry / failure policy: no retries.** The baseline does not retry
  delivery. An undeliverable message produces an `Error` (see
  Addressing and Routing); a handler that raises surfaces the failure
  to that handler's owner and does not cause redelivery. Justification:
  at-most-once with no persistence has nowhere durable to retry from,
  so retries are intentionally absent. A future durable transport may
  add at-least-once delivery and bounded retries behind the same
  transport interface without altering the protocol or these envelope
  semantics.

## Design Decisions & ADRs

The decisions behind this architecture are recorded as ADRs:

- [ADR 0002 — Transport architecture](../decisions/0002-transport-architecture.md):
  an abstract transport interface with a stdlib in-process transport
  (`asyncio` / `queue`) as the default first implementation.
- [ADR 0003 — Addressing and routing](../decisions/0003-addressing-and-routing.md):
  string addresses over `Metadata.sender` / `recipient`,
  broker-mediated routing, and undeliverable handling via `Error`.
- [ADR 0004 — Delivery semantics](../decisions/0004-delivery-semantics.md):
  at-most-once delivery, per-endpoint FIFO ordering, and a no-retry
  baseline failure policy.
- [ADR 0005 — HTTP+JSON network transport](../decisions/0005-http-json-transport.md):
  a stdlib HTTP+JSON transport implementing the ADR 0002 interface for
  distributed agents.

The concrete network transport that implements the ADR 0002 interface is
described in its own design note:
[Transport — HTTP+JSON network implementation](transport.md).

### Stdlib-first adherence

This design adheres to the stdlib-first principle established in
[ADR 0001 — Stdlib-first, minimal dependencies](../decisions/0001-stdlib-first.md).
The concrete stdlib facilities chosen are `asyncio` and `queue` for
the in-process transport's message passing and concurrency, and `json`
(already used by the protocol layer) for serialization. No runtime
dependency is introduced by any layer described here.
