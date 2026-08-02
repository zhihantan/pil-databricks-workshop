"""Lazily-constructed external clients (Databricks SDK, SQL warehouse, Lakebase).

Everything here degrades gracefully: if a client can't be built (running off
platform, Lakebase not enabled), the getter returns ``None`` and callers fall
back to demo/UC-only behaviour. Clients are cached per-process.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from backend.core.config import get_settings
from backend.core.logging import get_logger

LOG = get_logger("backend.clients")


@lru_cache
def workspace_client() -> Any | None:
    """Return an ambient WorkspaceClient, or None off-platform."""
    try:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient()
    except Exception as exc:  # noqa: BLE001
        LOG.warning("WorkspaceClient unavailable: %s", exc)
        return None


@lru_cache
def text_endpoint_name() -> str | None:
    """Resolve the governed FMAPI text endpoint via pil_workshop.llm (cached).

    Same endpoint the notebooks use, so app extraction traffic is governed and
    shows up on dashboard Page 4. Honors PIL_TEXT_ENDPOINT override.
    """
    try:
        from pil_workshop import llm

        return llm.resolve_endpoints(workspace_client()).text
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Could not resolve text endpoint: %s", exc)
        return None


@lru_cache
def sql_connection_params() -> tuple[str, str, str] | None:
    """Return (host, http_path, token) for the SQL connector, or None."""
    settings = get_settings()
    wc = workspace_client()
    if wc is None or not settings.warehouse_id:
        return None
    try:
        host = wc.config.host
        token = wc.config.token
        http_path = f"/sql/1.0/warehouses/{settings.warehouse_id}"
        return host, http_path, token
    except Exception as exc:  # noqa: BLE001
        LOG.warning("SQL connection params unavailable: %s", exc)
        return None


def sql_query(sql: str) -> list[dict[str, Any]]:
    """Run a UC read via the Databricks SQL connector; [] if unavailable."""
    params = sql_connection_params()
    if params is None:
        return []
    host, http_path, token = params
    try:
        from databricks import sql as dbsql

        with dbsql.connect(
            server_hostname=host.replace("https://", ""),
            http_path=http_path,
            access_token=token,
        ) as conn, conn.cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    except Exception as exc:  # noqa: BLE001
        LOG.warning("SQL query failed: %s", exc)
        return []


@lru_cache
def lakebase_available() -> bool:
    """True only if a Lakebase instance is actually reachable.

    The SDK always exposes a ``.database`` attribute, so attribute presence is
    not evidence of a provisioned/granted instance. We confirm by resolving the
    named instance (cached, so this runs at most once per process).
    """
    settings = get_settings()
    if settings.uc_only:
        return False
    wc = workspace_client()
    db = getattr(wc, "database", None) if wc else None
    if db is None:
        return False
    try:
        getter = getattr(db, "get_database_instance", None)
        if getter is None:
            return False
        getter(name=settings.lakebase_instance)
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.info("Lakebase instance '%s' not reachable: %s",
                 settings.lakebase_instance, exc)
        return False


def lakebase_connection() -> Any | None:
    """Open a Lakebase connection via SDK-minted credential, or None."""
    if not lakebase_available():
        return None
    try:
        from pil_workshop.lakebase import connect_via_credential

        return connect_via_credential(workspace_client(), get_settings().lakebase_instance)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Lakebase connection failed: %s", exc)
        return None
