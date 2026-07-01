"""Placeholder test demonstrating the tests/ layout.

Replace this with real tests for your package.
"""

import importlib.metadata


def test_version_is_a_string() -> None:
    assert isinstance(importlib.metadata.version("package_name"), str)
