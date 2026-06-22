"""Shared test helpers for the robotsix-agent-comm test suite."""

from __future__ import annotations

import os
from collections.abc import Callable

from robotsix_agent_comm.protocol import Message, Request, Response


def _write_certs_to_dir(tmpdir: str) -> tuple[str, str, str]:
    """Generate a self-signed cert via trustme and write PEM files.

    Returns ``(ca_cert_path, server_cert_path, server_key_path)``.
    """
    # trustme is an optional dev dependency; lazy-import so that
    # import-time failures only happen when the helper is actually called.
    import trustme  # noqa: F811

    ca = trustme.CA()
    server_cert = ca.issue_cert("127.0.0.1")

    ca_path = os.path.join(tmpdir, "ca.pem")
    cert_path = os.path.join(tmpdir, "server.pem")
    key_path = os.path.join(tmpdir, "server.key")

    with open(ca_path, "wb") as f:
        f.write(ca.cert_pem.bytes())
    with open(cert_path, "wb") as f:
        f.write(server_cert.cert_chain_pems[0].bytes())
    with open(key_path, "wb") as f:
        f.write(server_cert.private_key_pem.bytes())

    return ca_path, cert_path, key_path


def _echo_handler(
    received: list[Message],
) -> Callable[[Message], Message | None]:
    def handler(message: Message) -> Message | None:
        received.append(message)
        if isinstance(message, Request):
            return Response.to(message, body={"echo": message.body})
        return None

    return handler
