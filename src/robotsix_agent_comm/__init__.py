"""Agent communication stack for the robotsix ecosystem.

Provides four public sub-packages:

- ``protocol`` — typed message definitions (Request, Response,
  Notification, Error), serialization, and validation.
- ``transport`` — HTTP+JSON network transport with an in-memory
  Registry, TransportServer/TransportClient, RetryPolicy, and Router.
- ``sdk`` — high-level Agent client composing protocol + transport into
  a synchronous request-response and pub-sub API.
- ``chat`` — Starlette-based SSE chat server (create_app, run_server)
  plus the ChatAgent protocol for human-to-agent interaction.
"""

__version__ = "0.1.0"
