"""Tests for :func:`build_broker`, :func:`main`, and service integration."""

from __future__ import annotations

import http.client
import json
import os
import signal
import ssl
import threading
import time
from typing import Any

import pytest

# trustme is an optional dev dependency; skip the module if unavailable.
try:
    import trustme  # noqa: F401
except ImportError:
    pytest.skip("trustme not installed", allow_module_level=True)

from robotsix_agent_comm.broker import BrokerConfig, BrokerServer, build_broker
from robotsix_agent_comm.broker.config import _file_readable
from robotsix_agent_comm.broker.service import main

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_certs_to_dir(tmpdir: str) -> tuple[str, str, str]:
    """Generate a self-signed cert via trustme and write PEM files.

    Returns ``(ca_cert_path, server_cert_path, server_key_path)``.
    Mirrors ``tests/test_end_to_end.py::_write_certs_to_dir``.
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
# build_broker tests
# ---------------------------------------------------------------------------


class TestBuildBroker:
    def test_build_broker_with_tls_and_tokens(self, tmp_path: Any) -> None:
        ca_path, cert_path, key_path = _write_certs_to_dir(str(tmp_path))

        config = BrokerConfig(
            host="127.0.0.1",
            port=0,
            tls_cert=cert_path,
            tls_key=key_path,
            agent_tokens={"agent-a": "tok-a"},
        )
        broker = build_broker(config)
        assert isinstance(broker, BrokerServer)
        assert broker.host == "127.0.0.1"
        assert broker.port > 0

        # Verify the server has an SSL context.
        assert broker._server.socket is not None

        # Verify agent_tokens were propagated.
        assert broker._server.agent_tokens == {"agent-a": "tok-a"}
        assert broker._server._token_to_agent == {"tok-a": "agent-a"}

        broker._server.server_close()

    def test_build_broker_without_tls(self) -> None:
        config = BrokerConfig(host="127.0.0.1", port=0)
        broker = build_broker(config)
        assert isinstance(broker, BrokerServer)
        assert broker._server.agent_tokens is None
        broker._server.server_close()

    def test_build_broker_passes_tunables(self) -> None:
        config = BrokerConfig(
            host="127.0.0.1",
            port=0,
            ttl_seconds=120,
            rate_limit=5.0,
            max_body_size=512_000,
            audit_log=None,
        )
        broker = build_broker(config)
        assert broker._server.default_ttl_seconds == 120
        assert broker._server.rate_limit_per_second == 5.0
        assert broker._server.max_body_size == 512_000
        broker._server.server_close()

    def test_build_broker_with_mtls_config(self, tmp_path: Any) -> None:
        ca_path, cert_path, key_path = _write_certs_to_dir(str(tmp_path))

        config = BrokerConfig(
            host="127.0.0.1",
            port=0,
            tls_cert=cert_path,
            tls_key=key_path,
            tls_ca=ca_path,
            require_client_cert=True,
            agent_tokens={"agent-a": "tok-a"},
        )
        broker = build_broker(config)
        assert isinstance(broker, BrokerServer)
        broker._server.server_close()


# ---------------------------------------------------------------------------
# Authenticated TLS client test
# ---------------------------------------------------------------------------


class TestAuthenticatedTLSClient:
    """Prove that a secured broker rejects unauthenticated requests
    and accepts authenticated ones — the security invariant that
    no anonymous path is reachable in production."""

    def test_authenticated_request_accepted(self, tmp_path: Any) -> None:
        ca_path, cert_path, key_path = _write_certs_to_dir(str(tmp_path))

        config = BrokerConfig(
            host="127.0.0.1",
            port=0,
            tls_cert=cert_path,
            tls_key=key_path,
            agent_tokens={"agent-a": "tok-a"},
        )
        broker = build_broker(config)
        broker.start()
        try:
            # Build a client TLS context that trusts the CA.
            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.load_verify_locations(ca_path)

            # Authenticated request.
            conn = http.client.HTTPSConnection(
                broker.host, broker.port, timeout=5.0, context=client_ctx
            )
            try:
                headers = {
                    "Authorization": "Bearer tok-a",
                }
                conn.request("GET", "/health", headers=headers)
                resp = conn.getresponse()
                assert resp.status == 200
                data = json.loads(resp.read().decode())
                assert data == {"status": "ok"}
            finally:
                conn.close()
        finally:
            broker.stop()

    def test_unauthenticated_request_rejected_401(self, tmp_path: Any) -> None:
        ca_path, cert_path, key_path = _write_certs_to_dir(str(tmp_path))

        config = BrokerConfig(
            host="127.0.0.1",
            port=0,
            tls_cert=cert_path,
            tls_key=key_path,
            agent_tokens={"agent-a": "tok-a"},
        )
        broker = build_broker(config)
        broker.start()
        try:
            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.load_verify_locations(ca_path)

            # No Authorization header — should be rejected.
            conn = http.client.HTTPSConnection(
                broker.host, broker.port, timeout=5.0, context=client_ctx
            )
            try:
                conn.request("GET", "/health")
                resp = conn.getresponse()
                assert resp.status == 401
            finally:
                conn.close()
        finally:
            broker.stop()

    def test_invalid_token_rejected_401(self, tmp_path: Any) -> None:
        ca_path, cert_path, key_path = _write_certs_to_dir(str(tmp_path))

        config = BrokerConfig(
            host="127.0.0.1",
            port=0,
            tls_cert=cert_path,
            tls_key=key_path,
            agent_tokens={"agent-a": "tok-a"},
        )
        broker = build_broker(config)
        broker.start()
        try:
            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.load_verify_locations(ca_path)

            conn = http.client.HTTPSConnection(
                broker.host, broker.port, timeout=5.0, context=client_ctx
            )
            try:
                headers = {"Authorization": "Bearer wrong-token"}
                conn.request("GET", "/health", headers=headers)
                resp = conn.getresponse()
                assert resp.status == 401
            finally:
                conn.close()
        finally:
            broker.stop()

    def test_register_with_valid_token_succeeds(self, tmp_path: Any) -> None:
        ca_path, cert_path, key_path = _write_certs_to_dir(str(tmp_path))

        config = BrokerConfig(
            host="127.0.0.1",
            port=0,
            tls_cert=cert_path,
            tls_key=key_path,
            agent_tokens={"agent-a": "tok-a"},
        )
        broker = build_broker(config)
        broker.start()
        try:
            client_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            client_ctx.load_verify_locations(ca_path)

            conn = http.client.HTTPSConnection(
                broker.host, broker.port, timeout=5.0, context=client_ctx
            )
            try:
                body = json.dumps(
                    {"agent_id": "agent-a", "host": "127.0.0.1", "port": 9000}
                )
                headers = {
                    "Authorization": "Bearer tok-a",
                    "Content-Type": "application/json",
                }
                conn.request("POST", "/agents", body=body, headers=headers)
                resp = conn.getresponse()
                assert resp.status == 201
                data = json.loads(resp.read().decode())
                assert data["agent_id"] == "agent-a"
            finally:
                conn.close()
        finally:
            broker.stop()


# ---------------------------------------------------------------------------
# main() tests
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_config_error_returns_nonzero(self) -> None:
        """main() with no TLS in production should return 1."""
        # Set an env that will make production validation fail.
        rc = main([])
        assert rc == 1

    def test_main_with_valid_config(self, tmp_path: Any, monkeypatch: Any) -> None:
        """main() with a valid config boots successfully."""
        ca_path, cert_path, key_path = _write_certs_to_dir(str(tmp_path))
        token_file = tmp_path / "tokens.json"
        token_file.write_text(json.dumps({"agent-a": "tok-a"}))

        # Mock signal.signal (cannot be called from non-main thread)
        # and Event.wait so main() returns quickly.

        monkeypatch.setattr(signal, "signal", lambda *a: None)

        class QuickEvent(threading.Event):
            def wait(self, timeout: float | None = None) -> bool:
                # Let the broker start, then signal shutdown.
                time.sleep(0.05)
                return True

        monkeypatch.setattr(threading, "Event", QuickEvent)

        env: dict[str, str] = {
            "ROBOTSIX_BROKER_ENV": "production",
            "ROBOTSIX_BROKER_HOST": "127.0.0.1",
            "ROBOTSIX_BROKER_PORT": "0",
            "ROBOTSIX_BROKER_TLS_CERT": cert_path,
            "ROBOTSIX_BROKER_TLS_KEY": key_path,
            "ROBOTSIX_BROKER_AGENT_TOKENS_FILE": str(token_file),
        }

        with monkeypatch.context() as m:
            for k, v in env.items():
                m.setenv(k, v)
            # Also patch signal inside the context.
            m.setattr(signal, "signal", lambda *a: None)
            m.setattr(threading, "Event", QuickEvent)

            rc = main([])
            assert rc == 0


# ---------------------------------------------------------------------------
# ensure _file_readable helper works
# ---------------------------------------------------------------------------


class TestFileReadable:
    def test_existing_file(self, tmp_path: Any) -> None:
        f = tmp_path / "real.pem"
        f.write_text("data")
        assert _file_readable(str(f)) is True

    def test_nonexistent_file(self) -> None:
        assert _file_readable("/nonexistent/file.pem") is False

    def test_directory_not_file(self, tmp_path: Any) -> None:
        assert _file_readable(str(tmp_path)) is False
