"""Error and retry handling example.

Demonstrates two failure modes:

1. Sending to an unregistered recipient raises ``AgentNotFoundError``.
2. A handler returning ``Error.to(...)`` is delivered as an error reply the
   caller can inspect.

The agent's default ``RetryPolicy`` (exponential backoff) transparently
retries transient delivery failures before surfacing ``DeliveryError``.

Run with::

    python examples/error_handling.py
"""

from __future__ import annotations

from robotsix_agent_comm.protocol import Error, MessageType, Request
from robotsix_agent_comm.sdk import Agent
from robotsix_agent_comm.transport import AgentNotFoundError, Registry


def main() -> None:
    registry = Registry()

    service = Agent("service", registry)

    @service.on_request
    def handle(request: Request) -> Error:
        return Error.to(
            request,
            code="bad_request",
            message="this service always refuses",
        )

    client = Agent("client", registry)

    with service, client:
        # (a) Unknown recipient: AgentNotFoundError propagates to the caller.
        try:
            client.send_request("ghost", {"ping": True}, timeout=2.0)
        except AgentNotFoundError as exc:
            print(f"caught AgentNotFoundError: {exc}")

        # (b) Handler returns an error reply the caller inspects.
        reply = client.send_request("service", {"do": "something"}, timeout=2.0)
        if reply.type is MessageType.ERROR:
            print(f"error reply code: {reply.body['code']}")
            print(f"error reply message: {reply.body['message']}")


if __name__ == "__main__":
    main()
