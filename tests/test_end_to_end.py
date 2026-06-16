"""End-to-end integration test: cross-process agent communication via secured broker.

Starts the broker in-thread with TLS + auth. Spawns agent processes
(via :mod:`multiprocessing`) that register, discover each other, send
notifications, and complete request/response exchanges — fully exercising
the secured networked path.

Also verifies authentication failure (invalid token) and anti-spoofing
(agent trying to register as another identity).
"""

from __future__ import annotations

import contextlib
import multiprocessing
import os
import ssl
import tempfile
import time
import traceback
from collections.abc import Generator
from typing import Any

import pytest

# trustme is an optional dev dependency; skip the module if unavailable.
try:
    import trustme  # noqa: F401
except ImportError:
    pytest.skip("trustme not installed", allow_module_level=True)

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import (
    Message,
    Metadata,
    Notification,
    Request,
    Response,
)
from robotsix_agent_comm.transport import Endpoint, TransportServer
from robotsix_agent_comm.transport.brokered import (
    BrokeredRegistry,
    NetworkedBrokerTransport,
)

# ---------------------------------------------------------------------------
# TLS helpers
# ---------------------------------------------------------------------------


def _write_certs_to_dir(tmpdir: str) -> tuple[str, str, str]:
    """Generate a self-signed cert via trustme and write PEM files.

    Returns ``(ca_cert_path, server_cert_path, server_key_path)``.
    """
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


# ---------------------------------------------------------------------------
# Agent subprocess function  (module-level so multiprocessing can pickle it)
# ---------------------------------------------------------------------------


def _agent_process(
    agent_id: str,
    token: str,
    broker_host: str,
    broker_port: int,
    ca_cert_path: str,
    result_queue: multiprocessing.Queue[dict[str, Any]],
    ready_event: multiprocessing.synchronize.Event,
    start_event: multiprocessing.synchronize.Event,
    other_agent_id: str,
) -> None:
    """Run inside a child process: register, discover, exchange messages."""
    try:
        # -- Build TLS context from the CA cert file -----------------------
        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.load_verify_locations(ca_cert_path)

        # -- Build brokered components ------------------------------------
        registry = BrokeredRegistry(
            broker_host,
            broker_port,
            scheme="https",
            ssl_context=client_ctx,
            agent_token=token,
        )
        transport = NetworkedBrokerTransport(
            broker_host,
            broker_port,
            scheme="https",
            ssl_context=client_ctx,
            agent_token=token,
        )

        # -- Spin up a local TransportServer to receive messages ----------
        received: list[Message] = []

        def handler(message: Message) -> Message | None:
            received.append(message)
            if isinstance(message, Request):
                return Response.to(message, body={"echo": message.body})
            return None

        server = TransportServer(handler, host="127.0.0.1", port=0)
        server.start()

        try:
            endpoint = Endpoint(agent_id=agent_id, host=server.host, port=server.port)
            registry.register(endpoint)

            # Signal that we're registered and ready.
            ready_event.set()

            # Wait for the test to signal both agents are ready.
            start_event.wait(timeout=15)

            # -- Discovery: should see both agents ------------------------
            agents = registry.list_agents()
            agent_ids = {a.agent_id for a in agents}
            discovery_ok = other_agent_id in agent_ids and agent_id in agent_ids

            # -- Send a notification to the other agent -------------------
            note = Notification(
                metadata=Metadata.create(sender=agent_id, recipient=other_agent_id),
                body={"event": f"hello-from-{agent_id}"},
            )
            transport.send(note, endpoint, timeout=10.0)

            # -- Send a request to the other agent ------------------------
            request = Request(
                metadata=Metadata.create(sender=agent_id, recipient=other_agent_id),
                body={"action": f"ping-from-{agent_id}"},
            )
            reply = transport.send(request, endpoint, timeout=10.0)

            reply_ok = isinstance(reply, Response) and reply.body == {
                "echo": {"action": f"ping-from-{agent_id}"}
            }

            # Wait a moment for any inbound messages to arrive.
            time.sleep(0.5)

            # Check what we received.
            received_events = []
            received_requests = []
            for msg in received:
                if isinstance(msg, Notification):
                    received_events.append(msg.body.get("event"))
                elif isinstance(msg, Request):
                    received_requests.append(msg.body.get("action"))

            result_queue.put(
                {
                    "agent_id": agent_id,
                    "status": "ok",
                    "discovery_ok": discovery_ok,
                    "reply_ok": reply_ok,
                    "received_events": received_events,
                    "received_requests": received_requests,
                    "received_count": len(received),
                }
            )
        finally:
            server.stop()
    except Exception:
        result_queue.put(
            {
                "agent_id": agent_id,
                "status": "error",
                "traceback": traceback.format_exc(),
            }
        )


def _agent_process_auth_failure(
    agent_id: str,
    token: str,
    broker_host: str,
    broker_port: int,
    ca_cert_path: str,
    result_queue: multiprocessing.Queue[dict[str, Any]],
) -> None:
    """Attempt to register with an invalid token; report whether rejected."""
    try:
        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.load_verify_locations(ca_cert_path)

        registry = BrokeredRegistry(
            broker_host,
            broker_port,
            scheme="https",
            ssl_context=client_ctx,
            agent_token=token,
        )
        endpoint = Endpoint(agent_id=agent_id, host="127.0.0.1", port=19999)
        # register() is fire-and-forget (doesn't raise on 401/403).
        registry.register(endpoint)

        # Verify agent is NOT actually registered by trying to list agents
        # with the same (bad) token.  If auth rejects us, list_agents will
        # also fail.
        try:
            agents = registry.list_agents()
            # If we get here, the token was accepted.
            result_queue.put(
                {
                    "agent_id": agent_id,
                    "status": "ok",
                    "auth_rejected": False,
                    "agent_count": len(agents),
                }
            )
        except Exception:
            # list_agents raised — token was rejected.
            result_queue.put(
                {
                    "agent_id": agent_id,
                    "status": "ok",
                    "auth_rejected": True,
                }
            )
    except Exception:
        result_queue.put(
            {
                "agent_id": agent_id,
                "status": "error",
                "traceback": traceback.format_exc(),
            }
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def secured_broker() -> Generator[tuple[BrokerServer, str, str, str], None, None]:
    """Start a TLS+auth BrokerServer and return it along with cert paths.

    Yields ``(broker, ca_cert_path, server_cert_path, server_key_path)``.
    """
    tmpdir = tempfile.mkdtemp(prefix="e2e_tls_")
    ca_path, cert_path, key_path = _write_certs_to_dir(tmpdir)

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(cert_path, key_path)

    broker = BrokerServer(
        host="127.0.0.1",
        port=0,
        ssl_context=server_ctx,
        agent_tokens={
            "agent-a": "tok-a",
            "agent-b": "tok-b",
            "agent-admin": "tok-admin",
        },
    )
    broker.start()
    try:
        yield broker, ca_path, cert_path, key_path
    finally:
        broker.stop()
        # Clean up temp cert files.
        for p in (ca_path, cert_path, key_path):
            if os.path.exists(p):
                os.unlink(p)
        with contextlib.suppress(OSError):
            os.rmdir(tmpdir)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEndToEndSecured:
    """Full end-to-end: two agent processes communicate through a TLS+auth broker."""

    def test_two_agents_discover_and_exchange(
        self, secured_broker: tuple[BrokerServer, str, str, str]
    ) -> None:
        broker, ca_path, _cert_path, _key_path = secured_broker

        ctx = multiprocessing.get_context("spawn")
        result_queue: multiprocessing.Queue[dict[str, Any]] = ctx.Queue()
        ready_a = ctx.Event()
        ready_b = ctx.Event()
        start_event = ctx.Event()

        proc_a = ctx.Process(
            target=_agent_process,
            args=(
                "agent-a",
                "tok-a",
                broker.host,
                broker.port,
                ca_path,
                result_queue,
                ready_a,
                start_event,
                "agent-b",
            ),
            name="agent-a",
        )
        proc_b = ctx.Process(
            target=_agent_process,
            args=(
                "agent-b",
                "tok-b",
                broker.host,
                broker.port,
                ca_path,
                result_queue,
                ready_b,
                start_event,
                "agent-a",
            ),
            name="agent-b",
        )

        proc_a.start()
        proc_b.start()

        try:
            # Wait for both agents to register.
            ready_a.wait(timeout=15)
            ready_b.wait(timeout=15)

            # Give them a moment to settle, then signal to proceed.
            time.sleep(0.2)
            start_event.set()

            # Collect results.
            results: list[dict[str, Any]] = []
            deadline = time.monotonic() + 20
            while len(results) < 2 and time.monotonic() < deadline:
                with contextlib.suppress(Exception):
                    results.append(result_queue.get(timeout=1.0))

            assert len(results) == 2, (
                f"Expected 2 results, got {len(results)}: {results}"
            )

            for r in results:
                assert r["status"] == "ok", (
                    f"Agent {r.get('agent_id')} failed: {r.get('traceback', '')}"
                )
                assert r["discovery_ok"], (
                    f"Agent {r['agent_id']} discovery failed: did not see both agents"
                )
                assert r["reply_ok"], (
                    f"Agent {r['agent_id']} did not receive expected echo reply"
                )

            # Each agent should have received the other's notification + request.
            for r in results:
                other = "agent-b" if r["agent_id"] == "agent-a" else "agent-a"
                assert f"hello-from-{other}" in r["received_events"], (
                    f"Agent {r['agent_id']} did not receive notification from {other}"
                )
                assert f"ping-from-{other}" in r["received_requests"], (
                    f"Agent {r['agent_id']} did not receive request from {other}"
                )
        finally:
            for p in (proc_a, proc_b):
                if p.is_alive():
                    p.terminate()
                    p.join(timeout=5)

    def test_auth_failure_invalid_token_rejected(
        self, secured_broker: tuple[BrokerServer, str, str, str]
    ) -> None:
        """An agent with an invalid token cannot interact with the broker."""
        broker, ca_path, _cert_path, _key_path = secured_broker

        ctx = multiprocessing.get_context("spawn")
        result_queue: multiprocessing.Queue[dict[str, Any]] = ctx.Queue()

        proc = ctx.Process(
            target=_agent_process_auth_failure,
            args=(
                "agent-a",
                "bad-token",
                broker.host,
                broker.port,
                ca_path,
                result_queue,
            ),
            name="bad-auth-agent",
        )
        proc.start()
        try:
            proc.join(timeout=15)
            results: list[dict[str, Any]] = []
            while True:
                try:
                    results.append(result_queue.get_nowait())
                except Exception:
                    break

            assert len(results) >= 1, "No result from auth-failure agent"
            r = results[0]
            assert r["status"] == "ok", (
                f"Auth-failure agent error: {r.get('traceback')}"
            )
            assert r["auth_rejected"], (
                "Expected invalid token to be rejected, but it was accepted"
            )
        finally:
            if proc.is_alive():
                proc.terminate()
                proc.join(timeout=5)

    def test_anti_spoofing_register_as_other_id(
        self, secured_broker: tuple[BrokerServer, str, str, str]
    ) -> None:
        """An agent cannot register using another agent's identity."""
        broker, ca_path, _cert_path, _key_path = secured_broker

        # Use raw HTTP to have full visibility into broker state.
        import http.client
        import json as _json

        client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        client_ctx.load_verify_locations(ca_path)

        def _req(
            method: str,
            path: str,
            body: dict[str, Any] | None = None,
            token: str | None = None,
        ) -> tuple[int, Any]:
            conn = http.client.HTTPSConnection(
                broker.host, broker.port, timeout=5.0, context=client_ctx
            )
            try:
                headers: dict[str, str] = {}
                if body is not None:
                    headers["Content-Type"] = "application/json"
                if token is not None:
                    headers["Authorization"] = f"Bearer {token}"
                payload = _json.dumps(body).encode() if body is not None else None
                conn.request(method, path, body=payload, headers=headers)
                resp = conn.getresponse()
                data = resp.read().decode()
                parsed = _json.loads(data) if data else None
                return resp.status, parsed
            finally:
                conn.close()

        # 1. Legitimately register agent-a with tok-a.
        status, body = _req(
            "POST",
            "/agents",
            {
                "agent_id": "agent-a",
                "host": "127.0.0.1",
                "port": 19000,
                "capabilities": {"role": "legit"},
            },
            token="tok-a",
        )
        assert status == 201, f"Legitimate registration failed: {body}"

        # 2. Verify agent-a is visible with its capabilities.
        status, body = _req("GET", "/agents", token="tok-a")
        assert status == 200
        agents = body.get("agents", [])
        assert len(agents) == 1
        assert agents[0]["agent_id"] == "agent-a"
        assert agents[0]["capabilities"] == {"role": "legit"}

        # 3. Attempt to register as agent-a using tok-b (spoofing).
        status, body = _req(
            "POST",
            "/agents",
            {
                "agent_id": "agent-a",
                "host": "127.0.0.1",
                "port": 19999,
                "capabilities": {"role": "spoofed"},
            },
            token="tok-b",
        )
        assert status == 403, f"Expected 403 for spoof attempt, got {status}: {body}"
        assert "agent_id does not match token" in body.get("error", "")

        # 4. Verify agent-a still has its original capabilities (not the
        #    spoofed ones) and no extra agents exist.
        status, body = _req("GET", "/agents", token="tok-a")
        assert status == 200
        agents = body.get("agents", [])
        assert len(agents) == 1, (
            f"Expected exactly 1 agent after spoof attempt, got {agents}"
        )
        assert agents[0]["agent_id"] == "agent-a"
        assert agents[0]["capabilities"] == {"role": "legit"}, (
            f"agent-a capabilities were overwritten by spoof attempt: {agents[0]}"
        )
