# 2. Transport architecture

- Status: Accepted
- Date: 2026-06-14

## Context

The protocol layer (`src/robotsix_agent_comm/protocol/`) defines a
transport-agnostic `Message` envelope and its JSON serialization, but
nothing that actually moves a message from one agent to another. The
system needs a transport layer, and it needs to keep that layer
decoupled from both the protocol below it and the broker/client above
it so that alternative transports (in-process, inter-process, network)
can be swapped without changing message semantics.

Per ADR 0001 (stdlib-first, minimal dependencies), a transport must be
buildable on the standard library before any third-party messaging
dependency is considered.

## Decision

Introduce an **abstract transport interface** that the broker and
client API depend on, and a single concrete implementation as the
default first transport: a **stdlib in-process transport** built on
`asyncio` / `queue`.

- The transport's responsibility is narrow: move serialized `Message`
    envelopes between endpoints. It does not resolve addresses (that is
    the broker's job) and does not interpret message bodies.
- The first implementation is in-process and uses standard-library
    primitives only — `asyncio.Queue` / `queue.Queue` for handoff and
    `asyncio` for concurrency — directly honouring ADR 0001. No runtime
    dependency is added.
- Future transports (e.g. inter-process or network) implement the same
    interface, so the broker and client API are written once against the
    abstraction.

## Consequences

- The broker/router and client API code (later epic children) program
    to the transport interface, not to a concrete queue, keeping the
    layering clean.
- The baseline transport's guarantees (at-most-once, per-endpoint FIFO,
    no retries) follow from the chosen stdlib primitives; see ADR 0004.
- Adding a richer transport later is an additive change behind the same
    interface and does not touch the protocol envelope.
- Any transport that needs a third-party dependency must reference and
    update ADR 0001 before being adopted.
