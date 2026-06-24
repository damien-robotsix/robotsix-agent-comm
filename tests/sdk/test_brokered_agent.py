"""Tests for the BrokeredAgent convenience client (over a real in-process broker)."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Generator

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import Message, Metadata, Request, Response
from robotsix_agent_comm.sdk import BrokeredAgent
from robotsix_agent_comm.transport import Endpoint
from robotsix_agent_comm.transport.brokered import (
    NetworkedBrokerTransport,
)


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


def test_handler_offloaded_poll_thread_not_blocked() -> None:
    """A slow handler must not block the poll thread, preventing eviction.

    Regression: the pull receive-loop dispatched handlers inline on the poll
    thread, so a handler running longer than the broker TTL starved the
    heartbeat → the broker evicted the mailbox → a concurrent ``send`` got
    ``404 unknown recipient``.  Now handlers are offloaded to a worker pool,
    the poll thread returns immediately, and the heartbeat stays fresh.
    """
    ttl = 1.0
    sweep = 0.2
    grace = 2.0  # enough to absorb the sleep below
    broker = BrokerServer(
        host="127.0.0.1",
        port=0,
        ttl_seconds=int(ttl),
        sweep_interval_seconds=sweep,
        mailbox_grace_seconds=grace,
    )
    broker.start()
    try:
        # -- responder with a handler that sleeps well past the TTL --
        handler_started = threading.Event()
        handler_done = threading.Event()

        def slow_handler(request: Request) -> Message:
            handler_started.set()
            time.sleep(3.0)  # 3× the TTL
            handler_done.set()
            return Response.to(request, body={"slow": True})

        responder = BrokeredAgent(
            "responder",
            broker_host=broker.host,
            broker_port=broker.port,
            broker_scheme="http",
            broker_token=None,
            timeout=10.0,
            on_request=slow_handler,
        )

        requester = BrokeredAgent(
            "requester",
            broker_host=broker.host,
            broker_port=broker.port,
            broker_scheme="http",
            broker_token=None,
            timeout=10.0,
        )

        with responder, requester:
            # Fire the first request in a background thread so we can
            # observe state while the handler sleeps.
            reply_holder: list[Message] = []

            def _send_first() -> None:
                reply_holder.append(
                    requester.send_request(
                        "responder", {"action": "first"}, timeout=10.0
                    )
                )

            t = threading.Thread(target=_send_first, daemon=True)
            t.start()

            # Wait until the handler is definitely sleeping.
            assert handler_started.wait(5.0), "handler did not start"

            # Let the TTL elapse + at least one sweep run.
            time.sleep(ttl + sweep + 0.3)

            # (a) Responder must still be visible — no eviction.
            agents_before = _get_agents(broker)
            assert any(a.get("agent_id") == "responder" for a in agents_before), (
                f"responder evicted! agents={agents_before}"
            )

            # (b) A second inbound message sent during the sleep is
            # delivered (queued in mailbox, 202), not 404.
            transport = NetworkedBrokerTransport(broker.host, broker.port)
            second = Request(
                metadata=Metadata.create(sender="requester", recipient="responder"),
                body={"action": "second"},
            )
            # send returns None for 202 (queued) — no DeliveryError means
            # the recipient was found.
            result = transport.send(
                second,
                Endpoint(agent_id="responder", host="broker", port=0),
                timeout=5.0,
            )
            # 202 → None from NetworkedBrokerTransport.send
            assert result is None, f"expected 202 queued, got {result}"

            # Wait for the first handler to finish.
            t.join(timeout=10.0)
            assert not t.is_alive(), "first request hung"
            assert handler_done.is_set(), "handler did not complete"
            assert len(reply_holder) == 1
            assert reply_holder[0].body == {"slow": True}

            # After the handler finishes, the responder is still there.
            agents_after = _get_agents(broker)
            assert any(a.get("agent_id") == "responder" for a in agents_after), (
                f"responder evicted after handler! agents={agents_after}"
            )
    finally:
        broker.stop()


def _get_agents(broker: BrokerServer) -> list[dict[str, object]]:
    """Return the agent list from the broker's ``GET /agents``."""
    import http.client

    conn = http.client.HTTPConnection(broker.host, broker.port, timeout=5.0)
    try:
        conn.request("GET", "/agents")
        resp = conn.getresponse()
        data = resp.read().decode("utf-8")
        parsed = json.loads(data) if data else {}
        agents = parsed.get("agents", []) if isinstance(parsed, dict) else []
        return agents
    finally:
        conn.close()
