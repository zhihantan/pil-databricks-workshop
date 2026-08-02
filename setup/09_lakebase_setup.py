# Databricks notebook source
# MAGIC %md
# MAGIC # 09 · Lakebase (Postgres OLTP) Setup
# MAGIC
# MAGIC Provisions the operational store for the app and closes the analytics→ops→
# MAGIC analytics loop:
# MAGIC
# MAGIC * Creates a **Lakebase (managed Postgres) instance** via the SDK.
# MAGIC * Creates schema `pil_app` with OLTP tables: `invoice_review_queue`,
# MAGIC   `invoice_decisions`, `inspection_work_orders`, `app_audit_log`.
# MAGIC * Seeds the review queue from `gold.invoice_exceptions`.
# MAGIC * Sets up a **synced table** back to UC (`gold.invoice_decisions_synced`) so
# MAGIC   human decisions flow into analytics (reverse-ETL story).
# MAGIC
# MAGIC Credentials are minted via the SDK's database-credential API — **never**
# MAGIC hardcoded. If Lakebase isn't enabled in the workspace, the notebook prints
# MAGIC enablement guidance and continues (the app can run against UC-only in demo).

# COMMAND ----------

import os
import sys


def _add_repo_src_to_path() -> str:
    here = os.getcwd()
    probe = here
    for _ in range(6):
        if os.path.isdir(os.path.join(probe, "src", "pil_workshop")):
            src = os.path.join(probe, "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            return probe
        probe = os.path.dirname(probe)
    src = os.path.abspath(os.path.join(here, "..", "src"))
    if src not in sys.path:
        sys.path.insert(0, src)
    return os.path.dirname(src)


_add_repo_src_to_path()

from databricks.sdk import WorkspaceClient

from pil_workshop import config, dbx_api
from pil_workshop.lakebase import (
    DDL_STATEMENTS,
    connect_via_credential,
    seed_review_queue_rows,
)
from pil_workshop.utils import banner, ok, safe_identifier, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.text("lakebase_instance", "pil-workshop-db", "Lakebase instance name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
INSTANCE = dbutils.widgets.get("lakebase_instance") or "pil-workshop-db"

wc = WorkspaceClient()
banner(f"09 · Lakebase setup — instance '{INSTANCE}'")

# COMMAND ----------

# MAGIC %md ### Create the Lakebase instance (idempotent)

# COMMAND ----------

instance = None
try:
    instance = dbx_api.ensure_database_instance(INSTANCE, client=wc)
    if instance is None:
        warn("Lakebase Database API not available in this workspace.")
        print("  Enable Lakebase: Account Console → Previews / workspace settings, "
              "then re-run. The app can run UC-only for the demo meanwhile.")
    else:
        ok(f"Lakebase instance ready: {getattr(instance, 'name', INSTANCE)}")
except Exception as exc:  # noqa: BLE001
    warn(f"Could not ensure Lakebase instance: {exc}")

# COMMAND ----------

# MAGIC %md ### Create schema + OLTP tables and seed the review queue
# MAGIC We connect using a **short-lived credential minted by the SDK** (never a
# MAGIC hardcoded password), create the `pil_app` schema and tables, then seed the
# MAGIC queue from `gold.invoice_exceptions`.

# COMMAND ----------

seed_rows = []
try:
    rows = spark.sql(f"""
        SELECT file_name, invoice_no, customer, total, gt_total, exception_type
        FROM `{CATALOG}`.`gold`.`invoice_exceptions`
    """).collect()
    seed_rows = seed_review_queue_rows(rows)
    ok(f"Prepared {len(seed_rows)} review-queue rows from gold.invoice_exceptions")
except Exception as exc:  # noqa: BLE001
    warn(f"Could not read gold.invoice_exceptions (run 08 first?): {exc}")

if instance is not None:
    try:
        conn = connect_via_credential(wc, INSTANCE)
        with conn.cursor() as cur:
            for stmt in DDL_STATEMENTS:
                cur.execute(stmt)
            for r in seed_rows:
                cur.execute(
                    "INSERT INTO pil_app.invoice_review_queue "
                    "(file_name, invoice_no, customer, extracted_total, "
                    " ground_truth_total, exception_type, status) "
                    "VALUES (%s,%s,%s,%s,%s,%s,'pending') "
                    "ON CONFLICT (file_name) DO NOTHING",
                    (r["file_name"], r["invoice_no"], r["customer"],
                     r["extracted_total"], r["ground_truth_total"],
                     r["exception_type"]),
                )
        conn.commit()
        conn.close()
        ok("Created pil_app schema + tables; seeded review queue.")
    except Exception as exc:  # noqa: BLE001
        warn(f"Lakebase DDL/seed step failed: {exc}")
else:
    print("  Skipping DDL/seed — no Lakebase instance. App will use UC-only mode.")

# COMMAND ----------

# MAGIC %md ### Synced table back to UC (reverse-ETL: decisions → analytics)
# MAGIC A UC table that mirrors app decisions so they show up in analytics. When
# MAGIC Lakebase synced-tables are available they keep this current automatically;
# MAGIC otherwise we create the UC target now and document the sync.

# COMMAND ----------

try:
    spark.sql(f"""
        CREATE TABLE IF NOT EXISTS `{CATALOG}`.`gold`.`invoice_decisions_synced` (
            decision_id BIGINT,
            file_name STRING,
            invoice_no STRING,
            decision STRING,
            reason STRING,
            decided_by STRING,
            decided_at TIMESTAMP
        ) COMMENT 'Reverse-ETL target: invoice review decisions from the app.'
    """)
    ok("gold.invoice_decisions_synced ready (bonus dashboard tile source).")
    print("  Configure a Lakebase → UC synced table from pil_app.invoice_decisions "
          "to this table (Catalog → Lakebase → Synced tables), or run a scheduled "
          "COPY. The reverse-ETL loop: exceptions → queue → human decision → gold.")
except Exception as exc:  # noqa: BLE001
    warn(f"Could not create synced UC table: {exc}")

dbutils.notebook.exit(f"09 complete · lakebase={INSTANCE}")
