"""Tests for the shared logging setup module."""

from __future__ import annotations

import logging

import pytest

from robotsix_agent_comm._logging import setup_logging


class TestSetupLogging:
    def test_default_level_is_info(self) -> None:
        setup_logging(force=True)
        assert logging.getLogger().level == logging.INFO

    def test_reads_log_level_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        # Reimport to pick up the env var at module scope
        import importlib

        from robotsix_agent_comm import _logging

        importlib.reload(_logging)
        _logging.setup_logging(force=True)
        assert logging.getLogger().level == logging.DEBUG

    def test_rejects_typo_level(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "VERBOSE")
        import importlib

        from robotsix_agent_comm import _logging

        importlib.reload(_logging)
        _logging.setup_logging(force=True)
        # "VERBOSE" is not a valid level -> fallback to INFO
        assert logging.getLogger().level == logging.INFO
