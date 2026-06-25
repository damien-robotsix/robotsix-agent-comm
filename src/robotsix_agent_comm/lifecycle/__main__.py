"""Entrypoint for ``python -m robotsix_agent_comm.lifecycle``."""

from __future__ import annotations

from .service import main

if __name__ == "__main__":
    raise SystemExit(main())
