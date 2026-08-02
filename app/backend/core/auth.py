"""Databricks Apps auth helpers.

The app authenticates as its service principal via the ambient SDK config. When
user-level auth is enabled, Databricks forwards the user's token in the
``X-Forwarded-Access-Token`` header (and identity in ``X-Forwarded-*``); we
expose helpers to read those for on-behalf-of-user calls and audit attribution.
No tokens are logged or persisted.
"""

from __future__ import annotations

from fastapi import Header


def current_user_email(
    x_forwarded_email: str | None = Header(default=None),
    x_forwarded_user: str | None = Header(default=None),
) -> str:
    """Best-effort caller identity from Databricks Apps forwarded headers."""
    return x_forwarded_email or x_forwarded_user or "workshop-user"


def forwarded_access_token(
    x_forwarded_access_token: str | None = Header(default=None),
) -> str | None:
    """Return the on-behalf-of-user access token if present (else None)."""
    return x_forwarded_access_token
