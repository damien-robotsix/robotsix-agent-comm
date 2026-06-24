"""Tests for the broker monitoring dashboard (``GET /dashboard``).

Exercises the HTML dashboard endpoint and query-param token auth
against a live ``BrokerServer``.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request as HTTPRequest
from urllib.request import urlopen

from robotsix_agent_comm.broker import BrokerServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get(
    server: BrokerServer,
    path: str,
    token: str | None = None,
    *,
    query_token: bool = False,
) -> tuple[int, bytes, str]:
    """GET *path* and return ``(status, body_bytes, content_type)``."""
    url = f"http://{server.host}:{server.port}{path}"
    params: dict[str, str] | None = None
    headers: dict[str, str] = {}
    if token is not None:
        if query_token:
            params = {"token": token}
        else:
            headers["Authorization"] = f"Bearer {token}"

    if params:
        qs = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{qs}"

    req = HTTPRequest(url, headers=headers)  # noqa: S310
    try:
        with urlopen(req) as resp:  # noqa: S310 — test-local only
            ct = resp.headers.get("Content-Type", "")
            return resp.status, resp.read(), ct
    except HTTPError as exc:
        ct = exc.headers.get("Content-Type", "")
        body = exc.read()
        exc.close()
        return exc.code, body, ct


# ---------------------------------------------------------------------------
# Dashboard enabled
# ---------------------------------------------------------------------------


class TestDashboardEnabled:
    def test_dashboard_returns_html_when_enabled(self) -> None:
        server = BrokerServer(host="127.0.0.1", port=0, dashboard_enabled=True)
        server.start()
        try:
            status, body, ct = _get(server, "/dashboard")
            assert status == 200
            assert "text/html" in ct
            html = body.decode("utf-8")
            # Stable markers from the HTML.
            assert "<title>Broker Dashboard" in html
            assert "Registered Agents" in html
            assert "Message Traffic" in html
            assert 'id="agents-tbody"' in html
            assert 'id="traffic-tbody"' in html
        finally:
            server.stop()

    def test_root_also_serves_dashboard(self) -> None:
        server = BrokerServer(host="127.0.0.1", port=0, dashboard_enabled=True)
        server.start()
        try:
            status, body, ct = _get(server, "/")
            assert status == 200
            assert "text/html" in ct
            html = body.decode("utf-8")
            assert "<title>Broker Dashboard" in html
        finally:
            server.stop()

    def test_dashboard_disabled_by_default(self, broker: BrokerServer) -> None:
        status, body, ct = _get(broker, "/dashboard")
        assert status == 404

    def test_dashboard_disabled_explicit(self) -> None:
        server = BrokerServer(host="127.0.0.1", port=0, dashboard_enabled=False)
        server.start()
        try:
            status, body, ct = _get(server, "/dashboard")
            assert status == 404
        finally:
            server.stop()

    def test_dashboard_content_type_is_html_utf8(self) -> None:
        server = BrokerServer(host="127.0.0.1", port=0, dashboard_enabled=True)
        server.start()
        try:
            _, _, ct = _get(server, "/dashboard")
            assert "charset=utf-8" in ct.lower()
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# Query-param token auth
# ---------------------------------------------------------------------------


class TestDashboardQueryTokenAuth:
    def test_dashboard_accepts_query_param_token(self) -> None:
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
            dashboard_enabled=True,
        )
        server.start()
        try:
            status, body, ct = _get(
                server, "/dashboard", token="tok-a", query_token=True
            )
            assert status == 200
            assert "text/html" in ct
        finally:
            server.stop()

    def test_dashboard_accepts_header_token(self) -> None:
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
            dashboard_enabled=True,
        )
        server.start()
        try:
            status, body, ct = _get(server, "/dashboard", token="tok-a")
            assert status == 200
            assert "text/html" in ct
        finally:
            server.stop()

    def test_dashboard_rejects_missing_token_when_auth_enabled(self) -> None:
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
            dashboard_enabled=True,
        )
        server.start()
        try:
            status, body, ct = _get(server, "/dashboard")
            assert status == 401
            resp = json.loads(body)
            assert "error" in resp
        finally:
            server.stop()

    def test_dashboard_rejects_invalid_query_token(self) -> None:
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
            dashboard_enabled=True,
        )
        server.start()
        try:
            status, body, ct = _get(
                server, "/dashboard", token="wrong", query_token=True
            )
            assert status == 401
        finally:
            server.stop()

    def test_agents_accepts_query_param_token(self) -> None:
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
        )
        server.start()
        try:
            url = f"http://{server.host}:{server.port}/agents?token=tok-a"
            req = HTTPRequest(url)  # noqa: S310
            with urlopen(req) as resp:  # noqa: S310 — test-local only
                assert resp.status == 200
                data: dict[str, Any] = json.loads(resp.read())
                assert "agents" in data
        finally:
            server.stop()

    def test_traffic_accepts_query_param_token(self) -> None:
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
        )
        server.start()
        try:
            url = f"http://{server.host}:{server.port}/traffic?token=tok-a"
            req = HTTPRequest(url)  # noqa: S310
            with urlopen(req) as resp:  # noqa: S310 — test-local only
                assert resp.status == 200
                data: dict[str, Any] = json.loads(resp.read())
                assert "traffic" in data
        finally:
            server.stop()

    def test_header_overrides_query_token_when_both_present(self) -> None:
        """Header is authoritative: a bad header fails even with good query token."""
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
            dashboard_enabled=True,
        )
        server.start()
        try:
            url = f"http://{server.host}:{server.port}/dashboard?token=tok-a"
            req = HTTPRequest(  # noqa: S310
                url, headers={"Authorization": "Bearer wrong"}
            )
            try:
                with urlopen(req) as resp:  # noqa: S310 — test-local only
                    assert resp.status == 401
            except HTTPError as exc:
                exc.close()
                assert exc.code == 401
        finally:
            server.stop()

    def test_post_agents_ignores_query_param_token(self) -> None:
        """Mutating routes must NOT accept query-param tokens."""
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
        )
        server.start()
        try:
            payload = json.dumps(
                {"agent_id": "alice", "host": "127.0.0.1", "port": 9000}
            ).encode("utf-8")
            url = f"http://{server.host}:{server.port}/agents?token=tok-a"
            req = HTTPRequest(  # noqa: S310
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            try:
                with urlopen(req) as resp:  # noqa: S310 — test-local only
                    assert resp.status == 401
            except HTTPError as exc:
                exc.close()
                assert exc.code == 401
        finally:
            server.stop()

    def test_agents_rejects_invalid_query_token(self) -> None:
        server = BrokerServer(
            host="127.0.0.1",
            port=0,
            agent_tokens={"agent-a": "tok-a"},
        )
        server.start()
        try:
            url = f"http://{server.host}:{server.port}/agents?token=bad"
            req = HTTPRequest(url)  # noqa: S310
            try:
                with urlopen(req) as resp:  # noqa: S310 — test-local only
                    assert resp.status == 401
            except HTTPError as exc:
                exc.close()
                assert exc.code == 401
        finally:
            server.stop()


# ---------------------------------------------------------------------------
# No auth — dashboard works anonymously
# ---------------------------------------------------------------------------


class TestDashboardNoAuth:
    def test_dashboard_works_without_tokens(self) -> None:
        server = BrokerServer(host="127.0.0.1", port=0, dashboard_enabled=True)
        server.start()
        try:
            status, body, ct = _get(server, "/dashboard")
            assert status == 200
            assert "text/html" in ct
        finally:
            server.stop()
