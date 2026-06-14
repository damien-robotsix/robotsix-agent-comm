"""Smoke test ensuring the package imports and exposes its version."""

import robotsix_agent_comm


def test_import_package() -> None:
    """The package imports and exposes a version string."""
    assert isinstance(robotsix_agent_comm.__version__, str)
