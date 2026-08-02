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


def _bearer_token(wc: Any) -> str | None:
    """Resolve a bearer token for the SQL connector.

    ``wc.config.token`` is ``None`` when the app authenticates via OAuth
    (``oauth-m2m`` — the default for a Databricks App service principal), so
    fall back to ``config.authenticate()`` which returns an ``Authorization``
    header for whatever auth the runtime has (OAuth, PAT, or ambient). Mirrors
    ``pil_workshop.llm.get_openai_client``.
    """
    token = getattr(wc.config, "token", None)
    if token:
        return token
    auth_header = wc.config.authenticate().get("Authorization", "")
    if auth_header.lower().startswith("bearer "):
        return auth_header.split(" ", 1)[1]
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
        token = _bearer_token(wc)
        if not token:
            LOG.warning("No bearer token available for SQL connector.")
            return None
        http_path = f"/sql/1.0/warehouses/{settings.warehouse_id}"
        return host, http_path, token
    except Exception as exc:  # noqa: BLE001
        LOG.warning("SQL connection params unavailable: %s", exc)
        return None


def _run_sql(sql: str) -> list[dict[str, Any]]:
    """Execute SQL via the Databricks SQL connector; raise on any failure."""
    params = sql_connection_params()
    if params is None:
        raise RuntimeError(
            "No SQL warehouse connection. Grant the app CAN USE on a serverless "
            "warehouse and set PIL_WAREHOUSE_ID."
        )
    host, http_path, token = params
    from databricks import sql as dbsql

    with dbsql.connect(
        server_hostname=host.replace("https://", ""),
        http_path=http_path,
        access_token=token,
    ) as conn, conn.cursor() as cur:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


def sql_query(sql: str) -> list[dict[str, Any]]:
    """Run a UC read via the Databricks SQL connector; [] if unavailable.

    Read paths (KPIs, lists) degrade gracefully to an empty result so the app
    still renders when a table/warehouse is missing. Paths that must surface
    failures (invoice extraction) call ``sql_query_strict`` instead.
    """
    try:
        return _run_sql(sql)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("SQL query failed (returning []): %s", exc)
        return []


def sql_query_strict(sql: str) -> list[dict[str, Any]]:
    """Run SQL and raise on failure (so the caller reports a real error)."""
    return _run_sql(sql)


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
