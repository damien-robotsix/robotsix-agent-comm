"""Tests for the BrokeredAgent convenience client (over a real in-process broker)."""

from __future__ import annotations

import threading
from collections.abc import Generator

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import Message, Request, Response
from robotsix_agent_comm.sdk import BrokeredAgent


@pytest.fixture
def broker() -> Generator[BrokerServer, None, None]:
    server = BrokerServer(host="127.0.0.1", port=0)
    server.start()
    try:
        yield server
    finally:
        server.stop()


def _agent(agent_id: str, broker: BrokerServer, **kw: object) -> BrokeredAgent:
    return BrokeredAgent(
        agent_id,
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=None,
        timeout=5.0,
        **kw,  # type: ignore[arg-type]
    )


def test_request_response(broker: BrokerServer) -> None:
    received: list[Message] = []

    def handle(request: Request) -> Message:
        received.append(request)
        return Response.to(request, body={"echo": request.body})

    responder = _agent("responder", broker, on_request=handle)
    requester = _agent("requester", broker)
    with responder, requester:
        reply = requester.send_request("responder", {"action": "ping"}, timeout=5.0)

    assert isinstance(reply, Response)
    assert reply.body == {"echo": {"action": "ping"}}
    assert len(received) == 1


def test_notification(broker: BrokerServer) -> None:
    got: list[Message] = []
    delivered = threading.Event()

    def on_notif(notification: Message) -> None:
        got.append(notification)
        delivered.set()

    listener = _agent("listener", broker, on_notification=on_notif)
    sender = _agent("sender", broker)
    with listener, sender:
        sender.send_notification("listener", {"event": "spike"})
        assert delivered.wait(5.0)

    assert got[0].body == {"event": "spike"}


def test_on_request_decorator(broker: BrokerServer) -> None:
    responder = _agent("r2", broker)

    @responder.on_request
    def _handle(request: Request) -> Message:
        return Response.to(request, body={"ok": True})

    requester = _agent("q2", broker)
    with responder, requester:
        reply = requester.send_request("r2", {}, timeout=5.0)

    assert reply.body == {"ok": True}


def test_tls_ca_builds_ssl_context(broker: BrokerServer, tmp_path: object) -> None:
    # A custom CA path is turned into an SSLContext at construction time.
    import datetime

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-ca")])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    ca = tmp_path / "ca.pem"  # type: ignore[operator]
    ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    agent = BrokeredAgent(
        "tls-client",
        broker_host="broker.example.com",
        broker_port=443,
        broker_scheme="https",
        broker_token="tok",
        tls_ca=str(ca),
    )
    assert agent.agent_id == "tls-client"
