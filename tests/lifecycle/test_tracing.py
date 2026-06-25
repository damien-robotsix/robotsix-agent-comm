"""Tests for :class:`LifecycleTracing` — Langfuse integration and no-op mode."""

from __future__ import annotations

import sys

import pytest

from robotsix_agent_comm.lifecycle.tracing import (
    _DUMMY,
    LifecycleTracing,
    _DummySpan,
)

# ---------------------------------------------------------------------------
# No-op / dummy mode tests (no Langfuse installed or no credentials)
# ---------------------------------------------------------------------------


class TestNoOpMode:
    """When Langfuse is unavailable or credentials are missing."""

    def test_disabled_when_credentials_missing(self) -> None:
        """Tracing is disabled when no keys are provided."""
        tracing = LifecycleTracing(public_key=None, secret_key=None)
        assert tracing.enabled is False

    def test_disabled_when_public_key_empty(self) -> None:
        """Tracing is disabled when public_key is empty string."""
        tracing = LifecycleTracing(public_key="", secret_key="secret")
        assert tracing.enabled is False

    def test_disabled_when_secret_key_empty(self) -> None:
        """Tracing is disabled when secret_key is empty string."""
        tracing = LifecycleTracing(public_key="pk", secret_key="")
        assert tracing.enabled is False

    def test_trace_returns_dummy_in_noop_mode(self) -> None:
        """trace() returns a _DummySpan in no-op mode."""
        tracing = LifecycleTracing()
        span = tracing.trace("test-op")
        assert isinstance(span, _DummySpan)
        assert span is _DUMMY

    def test_trace_context_manager_noop(self) -> None:
        """Dummy span works as a context manager."""
        tracing = LifecycleTracing()
        with tracing.trace("ctx-op") as span:
            span.event("inside")
            with span.span("child") as child:
                child.event("deep")
        # No exceptions = pass.

    def test_event_is_noop(self) -> None:
        """event() is a silent no-op when disabled."""
        tracing = LifecycleTracing()
        # Should not raise.
        tracing.event("test-event")
        tracing.event("test-event", trace_id="some-id")

    def test_dummy_span_update_is_noop(self) -> None:
        """DummySpan.update() is a no-op."""
        dummy = _DummySpan()
        dummy.update(foo="bar")
        # No exception = pass.

    def test_dummy_span_returns_self_for_span(self) -> None:
        """DummySpan.span() returns another _DummySpan."""
        dummy = _DummySpan()
        child = dummy.span("child")
        assert isinstance(child, _DummySpan)


# ---------------------------------------------------------------------------
# Disabled when langfuse package is not importable
# ---------------------------------------------------------------------------


class TestImportErrorDisablesTracing:
    """When langfuse cannot be imported, tracing stays disabled."""

    def test_import_error_disables_tracing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If 'import langfuse' raises ModuleNotFoundError, tracing is disabled."""
        # Simulate langfuse not installed by removing it from sys.modules
        # and stashing any real import.
        monkeypatch.setitem(sys.modules, "langfuse", None)

        # Re-import the tracing module to pick up the simulated state.
        # We need to force-reload the module.
        # The _HAS_LANGFUSE flag is set at module import time.
        # We can directly test the constructor's behaviour by checking
        # that it stays disabled even with credentials, since the module
        # flag is False.
        # Construct a new instance; it should check _HAS_LANGFUSE.
        # But the module was imported before the monkeypatch.
        # We need to reload it.
        import importlib

        import robotsix_agent_comm.lifecycle.tracing as tmod

        importlib.reload(tmod)

        # Now the module's _HAS_LANGFUSE should be False.
        assert tmod._HAS_LANGFUSE is False

        tracing = tmod.LifecycleTracing(
            public_key="pk-fake",
            secret_key="sk-fake",
            host="https://fake.langfuse.com",
        )
        assert tracing.enabled is False

        # Restore by re-reloading (or the next test may be affected).
        importlib.reload(tmod)
