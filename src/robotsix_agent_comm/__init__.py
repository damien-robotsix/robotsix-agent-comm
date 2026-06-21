"""Agent communication stack for the robotsix ecosystem.

Provides typed message protocols, an HTTP+JSON transport, and a high-level
Agent SDK — all using only the Python standard library.

See docs/modules.yaml for the canonical module taxonomy.
"""

from .errors import RobotsixAgentCommError

__all__ = ["RobotsixAgentCommError"]

__version__ = "0.1.0"
