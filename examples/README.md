# Examples

Runnable examples demonstrating the `robotsix-agent-comm` SDK. Each script
wires multiple `Agent` instances in one process sharing a single in-memory
`Registry` (the supported single-host topology) and can be run directly:

```bash
python examples/request_response.py
python examples/notification_listener.py
python examples/error_handling.py
```

- [`request_response.py`](request_response.py) — register two agents and
    send a request that awaits a correlated response.
- [`notification_listener.py`](notification_listener.py) — fire-and-forget
    notifications received via a callback and via the `receive_message` pull
    queue.
- [`error_handling.py`](error_handling.py) — catching `AgentNotFoundError`
    for unknown recipients, inspecting an `Error` reply, and the default
    retry policy.

See the [SDK tutorial](../docs/sdk/tutorial.md) for a full walkthrough.
