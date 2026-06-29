"""Smoke test ensuring the package imports and exposes its version."""

from importlib.metadata import version

import robotsix_agent_comm


def test_import_package() -> None:
    """The package imports successfully."""
    assert robotsix_agent_comm is not None


def test_package_version() -> None:
    """The package exposes a valid version string through importlib.metadata."""
    assert isinstance(version("robotsix-agent-comm"), str)
