"""Request-response example.

Registers two agents in one process sharing a single in-memory ``Registry``.
A requester sends a request and awaits the correlated response produced by
the responder's ``on_request`` handler.

Run with::

    python examples/request_response.py
"""

from __future__ import annotations

from robotsix_agent_comm.protocol import Request, Response
from robotsix_agent_comm.sdk import Agent
from robotsix_agent_comm.transport import Registry


def main() -> None:
    """Run the example."""
    registry = Registry()

    responder = Agent("responder", registry)

    @responder.on_request
    def handle(request: Request) -> Response:
        name = request.body.get("name", "world")
        return Response.to(request, body={"greeting": f"hello, {name}!"})

    requester = Agent("requester", registry)

    with responder, requester:
        reply = requester.send_request("responder", {"name": "agent"}, timeout=2.0)

    print(f"response body: {reply.body}")
    print(f"correlation id matches: {reply.correlation_id is not None}")


if __name__ == "__main__":
    main()
