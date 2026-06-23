"""Notification (fire-and-forget) example.

A listener agent receives notifications through an ``on_notification``
callback while a second agent fires a notification at it. The listener also
demonstrates the pull API via ``receive_message``.

Run with::

    python examples/notification_listener.py
"""

from __future__ import annotations

from robotsix_agent_comm.protocol import Notification
from robotsix_agent_comm.sdk import Agent
from robotsix_agent_comm.transport import Registry


def main() -> None:
    """Run the example."""
    registry = Registry()

    listener = Agent("listener", registry)

    @listener.on_notification
    def handle(notification: Notification) -> None:
        print(f"callback received: {notification.body}")

    publisher = Agent("publisher", registry)

    with listener, publisher:
        publisher.send_notification("listener", {"event": "tick", "seq": 1})
        # The same message is also queued for pull-style consumption.
        message = listener.receive_message(timeout=2.0)
        print(f"pulled from queue: {message.body}")


if __name__ == "__main__":
    main()
