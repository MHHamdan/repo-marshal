"""Route table."""

from __future__ import annotations

ROUTES: dict[str, str] = {
    "/health": "health",
    "/session/renew": "renew",
}


def resolve(path: str) -> str | None:
    return ROUTES.get(path)
