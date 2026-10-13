"""Session renewal."""

from __future__ import annotations

import time

LIFETIME_S = 900


def expires_at(issued_at: float) -> float:
    """When a session issued at `issued_at` stops being valid."""
    return issued_at + LIFETIME_S


def is_expired(issued_at: float, now: float | None = None) -> bool:
    current = time.time() if now is None else now
    return current >= expires_at(issued_at)
