"""Structured JSON audit logger."""

from __future__ import annotations

import json
import threading
import time
from typing import TextIO


class _AuditLogger:
    """Write structured JSON audit records to a file or stdout."""

    def __init__(self, path: str | None) -> None:
        self._file: TextIO | None = None
        self._lock = threading.Lock()
        if path is not None:
            self._file = open(path, "a", encoding="utf-8")  # noqa: SIM115

    def log(
        self,
        action: str,
        agent_id: str,
        *,
        path: str = "",
        status: int = 0,
        detail: str = "",
    ) -> None:
        record = {
            "timestamp": time.time(),
            "action": action,
            "agent_id": agent_id,
            "path": path,
            "status": status,
            "detail": detail,
        }
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with self._lock:
            if self._file is not None:
                self._file.write(line)
                self._file.flush()
            else:
                import sys

                sys.stdout.write(line)
                sys.stdout.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
