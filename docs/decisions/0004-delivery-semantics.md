# 4. Delivery semantics

- Status: Accepted
- Date: 2026-06-14

## Context

The baseline transport (ADR 0002) is an in-process implementation built
on stdlib `asyncio` / `queue`. Callers and the broker need an explicit
statement of what delivery, ordering, and failure behaviour to expect,
so that later code children and applications do not assume guarantees
the baseline does not provide. ADR 0001 constrains these choices to
what the standard library offers without extra dependencies.

## Decision

Define the baseline in-process transport's semantics as follows:

- **Delivery guarantee: at-most-once.** A message is delivered to its
    endpoint zero or one time, with no acknowledgement or redelivery.
    Justification: this is exactly what an in-memory stdlib queue
    provides, requiring no persistence or dedup machinery (ADR 0001).
- **Ordering guarantee: per-endpoint FIFO.** Messages for a single
    endpoint are delivered in enqueue order; there is no global ordering
    across endpoints. Justification: a per-endpoint stdlib queue
    preserves enqueue order for free, whereas global ordering would need
    a serialization point the in-process design does not require.
- **Retry / failure policy: no retries.** The baseline does not retry
    delivery; an undeliverable message yields an `Error` (ADR 0003) and a
    raising handler surfaces to its owner without redelivery.
    Justification: at-most-once with no durable store has nowhere to
    retry from, so retries are intentionally absent.

## Consequences

- Applications that need stronger guarantees must layer them on a
    future durable transport, not on the baseline.
- Because these guarantees are properties of the transport, not the
    envelope, a later at-least-once transport with bounded retries can be
    introduced behind the same transport interface (ADR 0002) without
    changing the protocol or these message semantics.
- The semantics are consistent with ADR 0001: nothing here requires a
    runtime dependency.
