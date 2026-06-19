# SDK tutorial

This getting-started guide walks through the high-level
`robotsix_agent_comm.sdk` API: registering agents, request-response with
timeouts, callback vs pull receiving, and error/retry handling. The SDK
composes the `protocol` and `transport` layers so an agent can communicate
in a few lines.

Because the `Registry` is in-memory, agents in the same process share a
single `Registry` object — the supported single-host topology. Every code
snippet below mirrors a runnable script under
[`examples/`](https://github.com/robotsix/robotsix-agent-comm/tree/main/examples).

## Registration and lifecycle

An `Agent` is bound to one `agent_id` and injected with a shared `Registry`.
Calling `start()` binds an ephemeral port (`port=0`), starts a listener, and
registers the agent's endpoint; `stop()` reverse this. The agent is
also a context manager.

```python
from robotsix_agent_comm.sdk import Agent
from robotsix_agent_comm.transport import Registry

registry = Registry()
agent = Agent("agent-a", registry)
agent.start()
try:
    ...  # registry.lookup("agent-a") now resolves to the bound endpoint
finally:
    agent.stop()
```

## Request-response with timeouts

Register a handler with `on_request`; it receives a `Request` and returns a
reply built with `Response.to(...)`. The requester calls `send_request`,
which routes the message and returns the correlated reply. A per-call
`timeout` bounds the wait.

```python
from robotsix_agent_comm.protocol import Request, Response

responder = Agent("responder", registry)


@responder.on_request
def handle(request: Request) -> Response:
    name = request.body.get("name", "world")
    return Response.to(request, body={"greeting": f"hello, {name}!"})


requester = Agent("requester", registry)

with responder, requester:
    reply = requester.send_request("responder", {"name": "agent"}, timeout=2.0)
    print(reply.body)  # {"greeting": "hello, agent!"}
```

## Receiving: callbacks vs pull

Register `on_notification` to receive fire-and-forget notifications via a
callback. Every inbound message is *also* placed on an internal thread-safe
queue, so an agent that prefers a pull loop can call `receive_message`
instead (or in addition).

```python
from robotsix_agent_comm.protocol import Notification

listener = Agent("listener", registry)


@listener.on_notification
def handle(notification: Notification) -> None:
    print("callback:", notification.body)


publisher = Agent("publisher", registry)

with listener, publisher:
    publisher.send_notification("listener", {"event": "tick"})
    message = listener.receive_message(timeout=2.0)  # pull the same message
    print("pulled:", message.body)
```

## Error and retry handling

Transport errors propagate so callers can handle them. Sending to an
unregistered recipient raises `AgentNotFoundError`; a handler may return an
error reply with `Error.to(...)` that the caller inspects. The agent's
default `RetryPolicy` (exponential backoff, three attempts) transparently
retries transient delivery failures before raising `DeliveryError`.

```python
from robotsix_agent_comm.protocol import Error, MessageType, Request
from robotsix_agent_comm.transport import AgentNotFoundError

service = Agent("service", registry)


@service.on_request
def handle(request: Request) -> Error:
    return Error.to(request, code="bad_request", message="refused")


client = Agent("client", registry)

with service, client:
    try:
        client.send_request("ghost", {}, timeout=2.0)
    except AgentNotFoundError:
        print("no such agent")

    reply = client.send_request("service", {}, timeout=2.0)
    if reply.type is MessageType.ERROR:
        print(reply.body["code"])  # bad_request
```

To override the retry behaviour, pass a custom `RetryPolicy` to the
constructor:

```python
from robotsix_agent_comm.transport import RetryPolicy

agent = Agent(
    "agent-a",
    registry,
    retry_policy=RetryPolicy(max_attempts=5, base_delay=0.05, max_delay=1.0),
)
```
