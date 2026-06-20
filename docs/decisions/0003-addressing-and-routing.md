# 3. Addressing and routing

- Status: Accepted
- Date: 2026-06-14

## Context

Agents need a way to name one another and to have messages routed to
the right endpoint. The protocol layer already carries addressing
information: `Metadata.sender` (str) and `Metadata.recipient`
(str | None). The design must decide what an address is, how a message
is routed to it, and what happens when it cannot be delivered — without
inventing a new on-wire schema field (ADR 0001 keeps the surface
small).

## Decision

Use **string addresses over the existing `Metadata.sender` /
`Metadata.recipient` fields**, with **broker-mediated routing** and
**undeliverable handling via the protocol's `Error` message**.

- An agent address is a non-empty, case-sensitive string naming a
    single logical endpoint (e.g. `"agent-a"`, `"planner"`,
    `"team/worker-3"`). No new schema field is added; the existing
    string fields are the addressing primitive, and `Metadata.extra`
    remains available for application-defined routing hints.
- A message is targeted by setting `Metadata.recipient`. The broker
    maintains an address → destination registry and forwards each
    message over the transport to the destination registered for its
    `recipient`. Replies are routed back automatically because
    `Response.to` / `Error.to` swap `sender` and `recipient`.
- When a message is undeliverable — `recipient` is None/empty or names
    an unregistered address — the broker replies to the originator with
    `Error.to(message, code=..., message=...)`, whose body is built by
    `error_body(...)`. The canonical error `code` strings are
    `"unknown_recipient"` (address not registered) and
    `"missing_recipient"` (recipient None or empty).

## Consequences

- Addressing requires no protocol change: it reuses fields that already
    exist and ship today, consistent with ADR 0001.
- The broker is the single place that knows the registry, so endpoints
    can join and leave without callers learning physical destinations.
- Undeliverable handling reuses the existing `Error` envelope and its
    correlation mechanism, so callers observe failures through the same
    channel as normal replies; no separate error transport is needed.
- An undeliverable `Error` (no live originator) is dropped rather than
    re-reported, preventing error loops.
