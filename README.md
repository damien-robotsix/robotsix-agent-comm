# robotsix-agent-comm

**Agent communication stack for the robotsix ecosystem** — a Python library
providing a typed message protocol, an HTTP+JSON network transport, and a
high-level agent SDK.

`robotsix-agent-comm` lets you build distributed agent systems in pure Python:
define message schemas, register agents with an in-memory registry, and send
request-response and fire-and-forget messages with retry and timeout. The
entire stack uses **only the Python standard library** — zero mandatory
runtime dependencies.

> Looking for the browser + SSE chat server that used to live here? It moved
> to its own project, [`robotsix-chat`](https://github.com/damien-robotsix/robotsix-chat).

## Quick start

```bash
git clone https://github.com/robotsix/robotsix-agent-comm.git
cd robotsix-agent-comm
uv sync
```

## Agent SDK

The `Agent` class composes the protocol and transport layers into a
few-line developer API. Here is a complete request-response example
(adapted from [`examples/request_response.py`](examples/request_response.py)):

```python
from robotsix_agent_comm.protocol import Request, Response
from robotsix_agent_comm.sdk import Agent
from robotsix_agent_comm.transport import Registry

registry = Registry()

responder = Agent("responder", registry)


@responder.on_request
def handle(request: Request) -> Response:
    name = request.body.get("name", "world")
    return Response.to(request, body={"greeting": f"hello, {name}!"})


requester = Agent("requester", registry)

with responder, requester:
    reply = requester.send_request("responder", {"name": "agent"}, timeout=2.0)

print(reply.body)  # {'greeting': 'hello, agent!'}
```

Run it:

```bash
uv run python examples/request_response.py
```

More examples live under [`examples/`](examples/):

- [`notification_listener.py`](examples/notification_listener.py) —
  fire-and-forget notifications with callback and pull APIs.
- [`error_handling.py`](examples/error_handling.py) — catching
  `AgentNotFoundError`, inspecting `Error` replies, and the default retry
  policy.

## Package structure

| Package | Description |
|---|---|
| `robotsix_agent_comm.protocol` | Typed message definitions (`Request`, `Response`, `Notification`, `Error`), serialization, and validation — stdlib-only. |
| `robotsix_agent_comm.transport` | HTTP+JSON transport layer: `Registry`, `TransportServer`/`TransportClient`, `RetryPolicy`, and `Router` — also stdlib-only. |
| `robotsix_agent_comm.sdk` | High-level `Agent` client combining protocol + transport into a synchronous request-response and pub-sub API. |

## Configuration

The project does not use a `.env` file or a global `Settings` class.
Everything is configured through constructor parameters.

### `Agent`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `agent_id` | `str` | *(required)* | Unique identifier for this agent. |
| `registry` | `Registry` | *(required)* | Shared in-memory registry for endpoint discovery. |
| `host` | `str` | `"127.0.0.1"` | Host address the agent's transport server binds to. |
| `port` | `int` | `0` | Port the transport server binds to (0 = OS picks). |
| `retry_policy` | `RetryPolicy \| None` | `RetryPolicy(max_attempts=3, base_delay=0.1, max_delay=2.0)` | Retry configuration for outbound messages. |
| `timeout` | `float` | `5.0` | Default timeout (seconds) for request-response. |

### `TransportServer`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `handler` | `MessageHandler` | *(required)* | Callback invoked with each deserialized inbound message. |
| `host` | `str` | `"127.0.0.1"` | Host address to bind. |
| `port` | `int` | `0` | Port to bind (0 = OS picks). |
| `message_path` | `str` | `"/messages"` | URL path on which POSTed messages are received. |

### `RetryPolicy`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `max_attempts` | `int` | *(required)* | Maximum number of delivery attempts (including the first). |
| `base_delay` | `float` | *(required)* | Initial backoff delay in seconds. |
| `max_delay` | `float` | *(required)* | Maximum backoff delay (capped). |
| `backoff_factor` | `float` | `2.0` | Multiplier applied after each attempt. |

## Development

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management. The lockfile (`uv.lock`) is committed; CI installs with
`uv sync --frozen`.

```bash
uv sync
```

### Running checks

```bash
uv run ruff check .              # lint
uv run ruff format --check .     # formatting
uv run mypy .                    # static type checking (strict)
uv run pytest                    # tests
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full development setup
including pre-commit hooks, branch conventions, and architecture
decisions.

## License and contributing

This project is licensed under the MIT License — see [`LICENSE`](LICENSE)
for the full text.

Contributions are welcome! Please read
[`CONTRIBUTING.md`](CONTRIBUTING.md) for development setup, coding
conventions, and pull request expectations. Architecture decisions are
documented in [`docs/decisions/`](docs/decisions/).
