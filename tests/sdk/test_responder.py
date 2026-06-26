"""Tests for BrokeredResponder dispatch, error framing, and auth integration."""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

import pytest

from robotsix_agent_comm.broker import BrokerServer
from robotsix_agent_comm.protocol import (
    Error,
    Message,
    ProtocolError,
    Request,
    Response,
)
from robotsix_agent_comm.sdk import BrokeredAgent, BrokeredResponder
from robotsix_agent_comm.sdk.responder import BUILTIN_HANDLERS

# ---------------------------------------------------------------------------
# Validated kind constants — keep in sync with BUILTIN_HANDLERS
# ---------------------------------------------------------------------------

_K_MONITOR = "monitor"
_K_CONFIG_GET = "config-get"
_K_CONFIG_SET = "config-set"

for _k in (_K_MONITOR, _K_CONFIG_GET, _K_CONFIG_SET):
    assert _k in BUILTIN_HANDLERS, f"Kind {_k!r} not in BUILTIN_HANDLERS"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _requester(
    agent_id: str,
    broker: BrokerServer,
    *,
    broker_token: str | None = None,
    **kw: object,
) -> BrokeredAgent:
    """Create a plain BrokeredAgent to drive requests against *broker*."""
    return BrokeredAgent(
        agent_id,
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=broker_token,
        timeout=5.0,
        **kw,  # type: ignore[arg-type]
    )


def _request(requester: BrokeredAgent, recipient: str, body: Any) -> Message:
    """Send a request and return the reply Message."""
    return requester.send_request(recipient, body, timeout=5.0)


# ---------------------------------------------------------------------------
# Concrete subclass for dispatch tests
# ---------------------------------------------------------------------------


class _TestResponder(BrokeredResponder):
    """A fully-wired responder for dispatch testing."""

    def __init__(
        self,
        agent_id: str,
        broker: BrokerServer,
        *,
        broker_token: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            agent_id,
            broker_host=broker.host,
            broker_port=broker.port,
            broker_scheme="http",
            broker_token=broker_token,
            timeout=5.0,
            **kwargs,
        )
        self._monitor_calls: list[dict[str, Any]] = []
        self._config: dict[str, Any] = {"theme": "dark", "retries": 3}

    def handle_monitor(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        self._monitor_calls.append(params)
        return {"cpu": 42, "mem": 99, "params": params}

    def handle_config_get(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        return dict(self._config)

    def handle_config_set(
        self, request: Request, params: dict[str, Any]
    ) -> dict[str, Any]:
        self._config.update(params)
        return dict(self._config)


# ---------------------------------------------------------------------------
# Constructor forwarding
# ---------------------------------------------------------------------------


def test_max_handler_workers_forwarded(broker: BrokerServer) -> None:
    """max_handler_workers is forwarded to Agent's handler worker pool."""
    r = BrokeredResponder(
        "r",
        broker_host=broker.host,
        broker_port=broker.port,
        broker_scheme="http",
        broker_token=None,
        timeout=5.0,
        max_handler_workers=10,
    )
    assert r._agent._max_handler_workers == 10


# ---------------------------------------------------------------------------
# Dispatch tests
# ---------------------------------------------------------------------------


def test_dispatch_monitor(broker: BrokerServer) -> None:
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        reply = _request(
            requester, "r", {"kind": _K_MONITOR, "params": {"detail": "full"}}
        )

    assert isinstance(reply, Response)
    assert reply.body == {"cpu": 42, "mem": 99, "params": {"detail": "full"}}
    assert responder._monitor_calls == [{"detail": "full"}]


def test_dispatch_config_get(broker: BrokerServer) -> None:
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        reply = _request(requester, "r", {"kind": _K_CONFIG_GET})

    assert isinstance(reply, Response)
    assert reply.body == {"theme": "dark", "retries": 3}


def test_dispatch_config_set_round_trip(broker: BrokerServer) -> None:
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        # -- apply a config update --
        reply_set = _request(
            requester,
            "r",
            {"kind": _K_CONFIG_SET, "params": {"retries": 7, "theme": "light"}},
        )
        assert isinstance(reply_set, Response)
        assert reply_set.body == {"theme": "light", "retries": 7}

        # -- verify the update persisted --
        reply_get = _request(requester, "r", {"kind": _K_CONFIG_GET})
        assert isinstance(reply_get, Response)
        assert reply_get.body == {"theme": "light", "retries": 7}


def test_missing_params_defaults_to_empty(broker: BrokerServer) -> None:
    """A request without 'params' should pass an empty dict to the handler."""
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        reply = _request(requester, "r", {"kind": _K_MONITOR})  # no params

    assert isinstance(reply, Response)
    assert reply.body["params"] == {}


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


def test_unknown_kind(broker: BrokerServer) -> None:
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        reply = _request(requester, "r", {"kind": "bogus"})

    assert isinstance(reply, Error)
    assert reply.body.get("code") == "unknown_kind"
    assert "bogus" in reply.body.get("message", "")


def test_malformed_body_not_a_dict(broker: BrokerServer) -> None:
    """Body that is not a dict should produce invalid_request.

    Because the SDK client helpers always wrap bodies in a dict, this
    edge case is tested by calling ``_dispatch`` directly with a crafted
    :class:`Request` that carries a list body.
    """
    from robotsix_agent_comm.protocol import Metadata
    from robotsix_agent_comm.protocol import Request as Req

    responder = _TestResponder("r", broker)
    request = Req(
        metadata=Metadata.create(sender="q", recipient="r"),
        body=["not", "a", "dict"],  # type: ignore[arg-type]
    )
    reply = responder._dispatch(request)

    assert isinstance(reply, Error)
    assert reply.body.get("code") == "invalid_request"


def test_missing_kind_key(broker: BrokerServer) -> None:
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        reply = _request(requester, "r", {"msg": "no kind here"})

    assert isinstance(reply, Error)
    assert reply.body.get("code") == "invalid_request"


def test_kind_not_a_string(broker: BrokerServer) -> None:
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        reply = _request(requester, "r", {"kind": 123})

    assert isinstance(reply, Error)
    assert reply.body.get("code") == "invalid_request"


def test_handler_raises_and_responder_stays_alive(broker: BrokerServer) -> None:
    """A handler that raises must produce handler_error and NOT crash."""

    class _FragileResponder(BrokeredResponder):
        def __init__(self, agent_id: str, broker: BrokerServer) -> None:
            super().__init__(
                agent_id,
                broker_host=broker.host,
                broker_port=broker.port,
                broker_scheme="http",
                broker_token=None,
                timeout=5.0,
            )

        def handle_monitor(
            self, request: Request, params: dict[str, Any]
        ) -> dict[str, Any]:
            raise RuntimeError("simulated telemetry failure")

        def handle_config_get(
            self, request: Request, params: dict[str, Any]
        ) -> dict[str, Any]:
            return {"ok": True}

    responder = _FragileResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        # 1. The handler raises → error frame
        reply1 = _request(requester, "r", {"kind": _K_MONITOR})
        assert isinstance(reply1, Error)
        assert reply1.body.get("code") == "handler_error"
        assert "simulated telemetry failure" in reply1.body.get("message", "")

        # 2. The responder is still alive → serves a subsequent request
        reply2 = _request(requester, "r", {"kind": _K_CONFIG_GET})
        assert isinstance(reply2, Response)
        assert reply2.body == {"ok": True}


def test_unimplemented_kind_yields_handler_error(
    broker: BrokerServer,
) -> None:
    """NotImplementedError from a base handler → handler_error."""

    class _PartialResponder(BrokeredResponder):
        def __init__(self, agent_id: str, broker: BrokerServer) -> None:
            super().__init__(
                agent_id,
                broker_host=broker.host,
                broker_port=broker.port,
                broker_scheme="http",
                broker_token=None,
                timeout=5.0,
            )

        def handle_monitor(
            self, request: Request, params: dict[str, Any]
        ) -> dict[str, Any]:
            return {"cpu": 1}

        # config-get and config-set left unimplemented

    responder = _PartialResponder("r", broker)
    requester = _requester("q", broker)
    with responder, requester:
        # monitor works
        r1 = _request(requester, "r", {"kind": _K_MONITOR})
        assert isinstance(r1, Response)

        # config-get raises NotImplementedError → handler_error
        r2 = _request(requester, "r", {"kind": _K_CONFIG_GET})
        assert isinstance(r2, Error)
        assert r2.body.get("code") == "handler_error"
        expected = f"{BUILTIN_HANDLERS[_K_CONFIG_GET]} not implemented"
        assert expected in r2.body.get("message", "")


# ---------------------------------------------------------------------------
# register_handler (non-built-in kinds)
# ---------------------------------------------------------------------------


def test_register_handler_custom_kind(broker: BrokerServer) -> None:
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)

    @responder.register_handler("echo")
    def _echo(request: Request, params: dict[str, Any]) -> dict[str, Any]:
        return {"echo": params}

    with responder, requester:
        reply = _request(requester, "r", {"kind": "echo", "params": {"msg": "hello"}})
    assert isinstance(reply, Response)
    assert reply.body == {"echo": {"msg": "hello"}}


def test_register_handler_overrides_builtin(broker: BrokerServer) -> None:
    """Instance-registered handler takes precedence over built-in method."""
    responder = _TestResponder("r", broker)
    requester = _requester("q", broker)

    @responder.register_handler(_K_MONITOR)
    def _custom_monitor(request: Request, params: dict[str, Any]) -> dict[str, Any]:
        return {"custom": True}

    with responder, requester:
        reply = _request(requester, "r", {"kind": _K_MONITOR})
    assert isinstance(reply, Response)
    assert reply.body == {"custom": True}


# ---------------------------------------------------------------------------
# Auth tests
# ---------------------------------------------------------------------------


@pytest.fixture
def auth_broker() -> Generator[BrokerServer, None, None]:
    """Start a plain-HTTP broker with bearer-token auth enabled."""
    server = BrokerServer(
        host="127.0.0.1",
        port=0,
        agent_tokens={"responder": "tok-r", "requester": "tok-q"},
    )
    server.start()
    try:
        yield server
    finally:
        server.stop()


def test_auth_round_trip(auth_broker: BrokerServer) -> None:
    """Responder + requester with correct tokens complete a monitor round-trip."""
    responder = _TestResponder("responder", auth_broker, broker_token="tok-r")
    requester = _requester("requester", auth_broker, broker_token="tok-q")
    with responder, requester:
        reply = requester.send_request("responder", {"kind": _K_MONITOR}, timeout=5.0)

    assert isinstance(reply, Response)
    assert reply.body["cpu"] == 42


def test_auth_requester_missing_token_rejected(
    auth_broker: BrokerServer,
) -> None:
    """A requester without a token is rejected by the auth-enabled broker."""
    responder = _TestResponder("responder", auth_broker, broker_token="tok-r")
    # Requester with NO token — broker should return 401.
    requester = _requester("no-token-req", auth_broker, broker_token=None)
    with responder, requester, pytest.raises(ProtocolError):
        requester.send_request("responder", {"kind": _K_MONITOR}, timeout=5.0)


def test_auth_requester_wrong_token_rejected(
    auth_broker: BrokerServer,
) -> None:
    """A requester with an invalid token is rejected by the auth-enabled broker."""
    responder = _TestResponder("responder", auth_broker, broker_token="tok-r")
    requester = _requester("bad-req", auth_broker, broker_token="wrong-token")
    with responder, requester, pytest.raises(ProtocolError):
        requester.send_request("responder", {"kind": _K_MONITOR}, timeout=5.0)
