"""Lakebase (managed Postgres) helpers: OLTP DDL, SDK-minted-credential
connections, and seed-row shaping.

Credentials are always minted via the SDK database-credential API (short-lived
token used as the Postgres password); no password is ever hardcoded. The
connection helper is shared by notebook 09 and the app backend so both use the
same auth path.
"""

from __future__ import annotations

from typing import Any

# OLTP schema + tables for the app. `file_name` is unique so seeding is
# idempotent (ON CONFLICT DO NOTHING).
DDL_STATEMENTS: list[str] = [
    "CREATE SCHEMA IF NOT EXISTS pil_app",
    """
    CREATE TABLE IF NOT EXISTS pil_app.invoice_review_queue (
        id                 BIGSERIAL PRIMARY KEY,
        file_name          TEXT UNIQUE NOT NULL,
        invoice_no         TEXT,
        customer           TEXT,
        po_number          TEXT,
        currency           TEXT,
        extracted_total    DOUBLE PRECISION,
        ground_truth_total DOUBLE PRECISION,
        exception_type     TEXT,
        status             TEXT NOT NULL DEFAULT 'pending',
        created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    # Add the correction columns if the table pre-exists from an earlier run.
    "ALTER TABLE pil_app.invoice_review_queue ADD COLUMN IF NOT EXISTS po_number TEXT",
    "ALTER TABLE pil_app.invoice_review_queue ADD COLUMN IF NOT EXISTS currency TEXT",
    """
    CREATE TABLE IF NOT EXISTS pil_app.invoice_decisions (
        decision_id  BIGSERIAL PRIMARY KEY,
        file_name    TEXT NOT NULL,
        invoice_no   TEXT,
        decision     TEXT NOT NULL,          -- approved | rejected | adjusted
        reason       TEXT,
        adjusted_total DOUBLE PRECISION,
        decided_by   TEXT,
        decided_at   TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pil_app.inspection_work_orders (
        work_order_id BIGSERIAL PRIMARY KEY,
        file_name     TEXT NOT NULL,
        container_no  TEXT,
        damage        TEXT,
        damage_type   TEXT,
        action        TEXT,
        status        TEXT NOT NULL DEFAULT 'open',
        created_by    TEXT,
        created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pil_app.app_audit_log (
        audit_id   BIGSERIAL PRIMARY KEY,
        actor      TEXT,
        action     TEXT NOT NULL,
        entity     TEXT,
        detail     JSONB,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """,
]


def seed_review_queue_rows(rows: Any) -> list[dict[str, Any]]:
    """Shape Spark ``Row`` objects from gold.invoice_exceptions into dicts."""
    out: list[dict[str, Any]] = []
    for r in rows:
        d = r.asDict() if hasattr(r, "asDict") else dict(r)
        out.append({
            "file_name": d.get("file_name"),
            "invoice_no": d.get("invoice_no"),
            "customer": d.get("customer"),
            "extracted_total": d.get("total"),
            "ground_truth_total": d.get("gt_total"),
            "exception_type": d.get("exception_type"),
        })
    return out


def get_connection_params(client: Any, instance_name: str) -> dict[str, Any]:
    """Return psycopg connection params using an SDK-minted credential.

    The credential's token is used as the password (sslmode=require). Host/port
    come from the instance metadata. Raises if Lakebase is unavailable.
    """
    from .dbx_api import ensure_database_instance, get_database_credential

    instance = ensure_database_instance(instance_name, client=client)
    if instance is None:
        raise RuntimeError(
            "Lakebase Database API unavailable; cannot build connection params."
        )
    cred = get_database_credential(instance_name, client=client)
    token = getattr(cred, "token", None) if cred else None
    host = getattr(instance, "read_write_dns", None) or getattr(instance, "host", None)
    if not host or not token:
        raise RuntimeError(
            "Could not resolve Lakebase host/credential. Ensure the caller (or "
            "app service principal) can access the instance."
        )
    # The Postgres user is the caller's identity; the SDK token is the password.
    user = getattr(client.current_user.me(), "user_name", "databricks")
    return {
        "host": host,
        "port": 5432,
        "dbname": "databricks_postgres",
        "user": user,
        "password": token,
        "sslmode": "require",
    }


def connect_via_credential(client: Any, instance_name: str) -> Any:
    """Open a psycopg connection to Lakebase using a freshly minted credential."""
    try:
        import psycopg
    except ImportError:
        try:
            import psycopg2 as psycopg  # type: ignore
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "psycopg (v3) or psycopg2 is required to connect to Lakebase."
            ) from exc
    params = get_connection_params(client, instance_name)
    return psycopg.connect(**params)
