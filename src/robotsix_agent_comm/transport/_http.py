"""Shared HTTP request helpers for transport implementations.

Extracted from :mod:`~.client` and :mod:`~.brokered` to eliminate
the duplicated request/try/except/finally skeleton (jscpd clone
pairs 2–4).
"""

from __future__ import annotations

import http.client

from .endpoints import HEALTH_PATH
from .errors import TransportError, TransportTimeoutError


def _do_post(
    conn: http.client.HTTPConnection,
    path: str,
    body: bytes,
    headers: dict[str, str],
    timeout: float,
    label: str = "request",
) -> tuple[int, str]:
    """POST *body* to *path* on *conn*, handling common transport errors.

    Returns ``(status, body_text)`` and closes *conn* in ``finally``.
    """
    try:
        conn.request("POST", path, body=body, headers=headers)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8")
    except TimeoutError as exc:
        raise TransportTimeoutError(f"{label} timed out after {timeout}s") from exc
    except OSError as exc:
        raise TransportError(f"failed to reach {label}: {exc}") from exc
    finally:
        conn.close()


def _check_health(
    conn: http.client.HTTPConnection,
    headers: dict[str, str] | None = None,
) -> bool:
    """``GET /health`` on *conn*; return ``True`` on 200, ``False`` on error.

    Closes *conn* in ``finally``.
    """
    try:
        if headers is not None:
            conn.request("GET", HEALTH_PATH, headers=headers)
        else:
            conn.request("GET", HEALTH_PATH)
        response = conn.getresponse()
        response.read()
        return response.status == 200
    except OSError:
        return False
    finally:
        conn.close()
