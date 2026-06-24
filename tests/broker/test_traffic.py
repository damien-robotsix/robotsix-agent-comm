"""Tests for the broker traffic recorder and ``GET /traffic`` endpoint.

Exercises the in-memory traffic ring buffer and the ``/traffic`` JSON
endpoint via raw HTTP against a live ``BrokerServer`` (no SDK).
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request as HTTPRequest
from urllib.request import urlopen

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol.messages import (
    Metadata,
    Notification,
    Request,
)


def _traffic(server: BrokerServer, **params: str) -> list[dict[str, Any]]:
    """Call ``GET /traffic`` on *server*, return the ``traffic`` list."""
    qs_parts = [f"{k}={v}" for k, v in params.items()]
    qs = "&".join(qs_parts)
    url = f"http://{server.host}:{server.port}/traffic"
    if qs:
        url += f"?{qs}"
    try:
        with urlopen(url) as resp:  # noqa: S310 — test-local only
            assert resp.status == 200
            body: dict[str, Any] = json.loads(resp.read())
            return list(body["traffic"])
    except HTTPError as exc:
        body = json.loads(exc.read())
        exc.close()
        raise AssertionError(f"GET /traffic returned {exc.code}: {body}") from exc


def _traffic_with_auth(
    server: BrokerServer, token: str, **params: str
) -> tuple[int, dict[str, Any]]:
    """Call ``GET /traffic`` with a Bearer token, return (status, body)."""
    qs_parts = [f"{k}={v}" for k, v in params.items()]
    qs = "&".join(qs_parts)
    url = f"http://{server.host}:{server.port}/traffic"
    if qs:
        url += f"?{qs}"
    req = HTTPRequest(url, headers={"Authorization": f"Bearer {token}"})  # noqa: S310
    try:
        with urlopen(req) as resp:  # noqa: S310 — test-local only
            body: dict[str, Any] = json.loads(resp.read())
            return resp.status, body
    except HTTPError as exc:
        body = json.loads(exc.read())
        exc.close()
        return exc.code, body


def _send_message(
    server: BrokerServer,
    sender: str,
    recipient: str,
    body: dict[str, Any] | None = None,
    token: str | None = None,
    **extra: Any,
) -> int:
    """POST a Request and return the HTTP status."""
    msg = Request(
        metadata=Metadata.create(sender=sender, recipient=recipient, **extra),
        body=body if body is not None else {"key": "value"},
    )
    from robotsix_agent_comm.protocol import serialize

    payload = serialize(msg).encode("utf-8")
    url = f"http://{server.host}:{server.port}/messages"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = HTTPRequest(url, data=payload, headers=headers)  # noqa: S310
    try:
        with urlopen(req) as resp:  # noqa: S310 — test-local only
            return resp.status  # type: ignore[no-any-return]
    except HTTPError as exc:
        exc.close()  # close to avoid ResourceWarning
        return exc.code


def _register_agent(
    server: BrokerServer,
    agent_id: str,
    host: str = "127.0.0.1",
    port: int = 9000,
    mailbox: bool = False,
    token: str | None = None,
) -> int:
    """POST /agents and return the HTTP status."""
    body: dict[str, Any] = {"agent_id": agent_id, "host": host, "port": port}
    if mailbox:
        body["mailbox"] = True
    payload = json.dumps(body).encode("utf-8")
    url = f"http://{server.host}:{server.port}/agents"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    req = HTTPRequest(url, data=payload, headers=headers)  # noqa: S310
    try:
        with urlopen(req) as resp:  # noqa: S310 — test-local only
            return resp.status  # type: ignore[no-any-return]
    except HTTPError as exc:
        exc.close()  # close to avoid ResourceWarning
        return exc.code


# ---------------------------------------------------------------------------
# Traffic ring buffer
# ---------------------------------------------------------------------------


class TestTrafficEndpoint:
    def test_empty_buffer_returns_empty_list(self, broker: BrokerServer) -> None:
        records = _traffic(broker)
        assert records == []

    def test_queued_message_fields(self, broker: BrokerServer) -> None:
        _register_agent(broker, "sender", mailbox=True)
        _register_agent(broker, "receiver", mailbox=True)
        _send_message(broker, "sender", "receiver", {"msg": "hello"})

        records = _traffic(broker)
        assert len(records) == 1
        r = records[0]
        assert r["source"] == "sender"
        assert r["destination"] == "receiver"
        assert r["type"] == "request"
        assert r["disposition"] == "queued"
        assert r["status"] == 202
        assert "timestamp" in r
        assert "message_id" in r
        assert "body_size_bytes" in r
        # Payload NOT exposed
        assert "body" not in r
        assert "payload" not in r

    def test_unknown_recipient_appears_in_traffic(self, broker: BrokerServer) -> None:
        _register_agent(broker, "sender", mailbox=True)
        status = _send_message(broker, "sender", "nonexistent", {"msg": "hello"})
        assert status == 404

        records = _traffic(broker)
        assert len(records) == 1
        assert records[0]["disposition"] == "unknown_recipient"
        assert records[0]["status"] == 404
        assert records[0]["destination"] == "nonexistent"

    def test_rejected_sender_mismatch_appears_in_traffic(
        self,
    ) -> None:
        server = BrokerServer(
            host="127.0.0.1", port=0, agent_tokens={"agent-a": "tok-a"}
        )
        server.start()
        try:
            _register_agent(server, "agent-a", mailbox=True, token="tok-a")
            _register_agent(server, "agent-b", mailbox=True, token="tok-a")

            # Send with token for agent-a but claim to be agent-b
            msg = Request(
                metadata=Metadata.create(sender="agent-b", recipient="agent-a"),
                body={"key": "value"},
            )
            from robotsix_agent_comm.protocol import serialize

            payload = serialize(msg).encode("utf-8")
            url = f"http://{server.host}:{server.port}/messages"
            req = HTTPRequest(  # noqa: S310
                url,
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": "Bearer tok-a",
                },
            )
            try:
                with urlopen(req) as resp:  # noqa: S310 — test-local only
                    assert resp.status == 403
            except HTTPError as exc:
                exc.close()
                assert exc.code == 403

            _, body = _traffic_with_auth(server, "tok-a")
            records = list(body["traffic"])
            assert len(records) == 1
            assert records[0]["disposition"] == "rejected"
            assert records[0]["status"] == 403
            assert records[0]["source"] == "agent-b"
        finally:
            server.stop()

    def test_topic_is_extracted_from_extra(self, broker: BrokerServer) -> None:
        _register_agent(broker, "sender", mailbox=True)
        _register_agent(broker, "receiver", mailbox=True)
        _send_message(broker, "sender", "receiver", {"msg": "hello"}, topic="greetings")

        records = _traffic(broker)
        assert records[0]["topic"] == "greetings"

    def test_topic_is_null_when_not_in_extra(self, broker: BrokerServer) -> None:
        _register_agent(broker, "sender", mailbox=True)
        _register_agent(broker, "receiver", mailbox=True)
        _send_message(broker, "sender", "receiver")

        records = _traffic(broker)
        assert records[0]["topic"] is None

    def test_notification_appears_with_correct_type(self, broker: BrokerServer) -> None:
        _register_agent(broker, "sender", mailbox=True)
        _register_agent(broker, "receiver", mailbox=True)
        msg = Notification(
            metadata=Metadata.create(sender="sender", recipient="receiver"),
            body={"event": "ping"},
        )
        from robotsix_agent_comm.protocol import serialize

        payload = serialize(msg).encode("utf-8")
        url = f"http://{broker.host}:{broker.port}/messages"
        req = HTTPRequest(  # noqa: S310
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urlopen(req) as resp:  # noqa: S310 — test-local only
            assert resp.status == 202  # queued, not routed

        records = _traffic(broker)
        assert records[0]["type"] == "notification"


# ---------------------------------------------------------------------------
# Traffic filters
# ---------------------------------------------------------------------------


class TestTrafficFilters:
    def test_agent_filter_by_source(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        _register_agent(broker, "carol", mailbox=True)
        _send_message(broker, "alice", "carol")
        _send_message(broker, "bob", "carol")
        _send_message(broker, "carol", "alice")

        records = _traffic(broker, agent="alice")
        # alice→carol and carol→alice = 2 records involving alice
        assert len(records) == 2
        for r in records:
            assert r["source"] == "alice" or r["destination"] == "alice"

    def test_agent_filter_no_match(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        _send_message(broker, "alice", "bob")

        records = _traffic(broker, agent="nobody")
        assert records == []

    def test_topic_filter(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        _send_message(broker, "alice", "bob", topic="orders")
        _send_message(broker, "alice", "bob", topic="alerts")
        _send_message(broker, "alice", "bob")

        orders = _traffic(broker, topic="orders")
        assert len(orders) == 1
        assert orders[0]["topic"] == "orders"

        alerts = _traffic(broker, topic="alerts")
        assert len(alerts) == 1

        none_topic = _traffic(broker, topic="nonexistent")
        assert none_topic == []

    def test_since_filter(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        t0 = time.time()
        _send_message(broker, "alice", "bob", topic="first")
        time.sleep(0.05)
        _send_message(broker, "alice", "bob", topic="second")

        since = t0 + 0.025  # between the two messages
        records = _traffic(broker, since=str(since))
        assert len(records) == 1
        assert records[0]["topic"] == "second"

    def test_until_filter(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        _send_message(broker, "alice", "bob", topic="first")
        time.sleep(0.05)
        until = time.time()
        _send_message(broker, "alice", "bob", topic="second")

        records = _traffic(broker, until=str(until))
        assert len(records) == 1
        assert records[0]["topic"] == "first"

    def test_limit_filter(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        for i in range(10):
            _send_message(broker, "alice", "bob", {"n": i})

        records = _traffic(broker, limit="3")
        assert len(records) == 3

    def test_combined_filters(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        _register_agent(broker, "carol", mailbox=True)
        _send_message(broker, "alice", "bob", topic="orders")
        _send_message(broker, "alice", "carol", topic="orders")
        _send_message(broker, "bob", "carol", topic="alerts")

        records = _traffic(broker, agent="alice", topic="orders")
        assert len(records) == 2
        for r in records:
            assert r["topic"] == "orders"
            assert r["source"] == "alice" or r["destination"] == "alice"

    def test_malformed_since_ignored(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        _send_message(broker, "alice", "bob")

        # Malformed param should be ignored (still returns all records).
        records = _traffic(broker, since="not-a-number")
        assert len(records) == 1

    def test_malformed_limit_ignored(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        _send_message(broker, "alice", "bob")

        records = _traffic(broker, limit="abc")
        assert len(records) == 1

    def test_negative_limit_ignored(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        for i in range(5):
            _send_message(broker, "alice", "bob", {"n": i})

        records = _traffic(broker, limit="-3")
        # Negative limit is semantically invalid → ignored, returns all.
        assert len(records) == 5

    def test_zero_limit_ignored(self, broker: BrokerServer) -> None:
        _register_agent(broker, "alice", mailbox=True)
        _register_agent(broker, "bob", mailbox=True)
        _send_message(broker, "alice", "bob")

        records = _traffic(broker, limit="0")
        # Zero limit is semantically invalid → ignored, returns all.
        assert len(records) == 1


# ---------------------------------------------------------------------------
# Auth on /traffic
# ---------------------------------------------------------------------------


class TestTrafficAuth:
    def test_traffic_requires_auth_when_tokens_set(self) -> None:
        server = BrokerServer(
            host="127.0.0.1", port=0, agent_tokens={"agent-a": "tok-a"}
        )
        server.start()
        try:
            url = f"http://{server.host}:{server.port}/traffic"
            req = HTTPRequest(url)  # noqa: S310
            try:
                with urlopen(req) as resp:  # noqa: S310 — test-local only
                    assert resp.status == 401
            except HTTPError as exc:
                assert exc.code == 401
                body = json.loads(exc.read())
                exc.close()
                assert "error" in body
        finally:
            server.stop()

    def test_traffic_works_with_valid_token(self) -> None:
        server = BrokerServer(
            host="127.0.0.1", port=0, agent_tokens={"agent-a": "tok-a"}
        )
        server.start()
        try:
            status, body = _traffic_with_auth(server, "tok-a")
            assert status == 200
            assert body["traffic"] == []
        finally:
            server.stop()

    def test_traffic_rejects_invalid_token(self) -> None:
        server = BrokerServer(
            host="127.0.0.1", port=0, agent_tokens={"agent-a": "tok-a"}
        )
        server.start()
        try:
            url = f"http://{server.host}:{server.port}/traffic"
            req = HTTPRequest(  # noqa: S310
                url, headers={"Authorization": "Bearer wrong-token"}
            )
            try:
                with urlopen(req) as resp:  # noqa: S310 — test-local only
                    assert resp.status == 401
            except HTTPError as exc:
                exc.close()  # close to avoid ResourceWarning
                assert exc.code == 401
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# Buffer size / bounds
# ---------------------------------------------------------------------------


class TestTrafficBufferBounds:
    def test_custom_buffer_size_respected(self) -> None:
        server = BrokerServer(host="127.0.0.1", port=0, traffic_buffer_size=3)
        server.start()
        try:
            _register_agent(server, "a", mailbox=True)
            _register_agent(server, "b", mailbox=True)
            for i in range(5):
                _send_message(server, "a", "b", {"n": i})

            records = _traffic(server)
            assert len(records) == 3
        finally:
            server.stop()
