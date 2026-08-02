# Databricks notebook source
# MAGIC %md
# MAGIC # 99 · Teardown — Remove All Workshop Assets
# MAGIC
# MAGIC Removes everything the workshop created. **Destructive** — guarded by a
# MAGIC `confirm` widget you must set to the catalog name to proceed.
# MAGIC
# MAGIC Removes: the app, the Genie space (UI if API-created), the dashboard, the
# MAGIC Lakebase instance, and finally the **catalog** (`CASCADE`, which drops all
# MAGIC schemas, tables, views, and volumes).

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

from pil_workshop import config
from pil_workshop.utils import banner, ok, safe_identifier, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.text("confirm", "", "Type the catalog name to confirm teardown")
dbutils.widgets.text("app_name", "pil-invoice-vision", "App name")
dbutils.widgets.text("lakebase_instance", "pil-workshop-db", "Lakebase instance")

CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
CONFIRM = dbutils.widgets.get("confirm").strip()
APP_NAME = dbutils.widgets.get("app_name") or "pil-invoice-vision"
INSTANCE = dbutils.widgets.get("lakebase_instance") or "pil-workshop-db"

wc = WorkspaceClient()
banner(f"99 · Teardown of '{CATALOG}'")

if CONFIRM != CATALOG:
    warn(f"Confirmation required. Set the `confirm` widget to '{CATALOG}' to proceed. "
         "Nothing was deleted.")
    dbutils.notebook.exit("Teardown aborted — not confirmed.")

# COMMAND ----------

# MAGIC %md ### Delete the app

# COMMAND ----------

try:
    if getattr(wc, "apps", None) is not None:
        wc.apps.delete(name=APP_NAME)
        ok(f"Deleted app '{APP_NAME}'.")
    else:
        warn("Apps API absent — delete the app in the UI if it exists.")
except Exception as exc:  # noqa: BLE001
    warn(f"App delete skipped: {exc}")

# COMMAND ----------

# MAGIC %md ### Delete the Lakebase instance

# COMMAND ----------

try:
    db = getattr(wc, "database", None)
    if db is not None:
        db.delete_database_instance(name=INSTANCE)
        ok(f"Deleted Lakebase instance '{INSTANCE}'.")
    else:
        warn("Database API absent — no Lakebase instance to delete.")
except Exception as exc:  # noqa: BLE001
    warn(f"Lakebase delete skipped: {exc}")

# COMMAND ----------

# MAGIC %md ### Delete the ML model-serving endpoint
# MAGIC `11_ml_forecasting` may create a workspace-level serving endpoint — it lives
# MAGIC outside the catalog, so the CASCADE drop below won't remove it.

# COMMAND ----------

try:
    wc.serving_endpoints.delete(name="pil-spare-parts-forecaster")
    ok("Deleted serving endpoint 'pil-spare-parts-forecaster'.")
except Exception as exc:  # noqa: BLE001
    warn(f"Serving endpoint delete skipped (may not exist): {exc}")

# COMMAND ----------

# MAGIC %md ### Delete the dashboard(s) named 'PIL Operations'

# COMMAND ----------

try:
    for d in wc.lakeview.list():
        if getattr(d, "display_name", None) == "PIL Operations":
            wc.lakeview.trash(dashboard_id=d.dashboard_id)
            ok(f"Trashed dashboard {d.dashboard_id}.")
except Exception as exc:  # noqa: BLE001
    warn(f"Dashboard delete skipped: {exc}")

# COMMAND ----------

# MAGIC %md ### Drop the catalog (CASCADE)
# MAGIC This removes all schemas, tables, MVs, metric views, and volumes.

# COMMAND ----------

try:
    spark.sql(f"DROP CATALOG IF EXISTS `{CATALOG}` CASCADE")
    ok(f"Dropped catalog '{CATALOG}' (CASCADE).")
except Exception as exc:  # noqa: BLE001
    warn(f"Catalog drop failed: {exc}")

# COMMAND ----------

# MAGIC %md ### Genie space
# MAGIC If the Genie space was created via the UI, delete it in the Genie UI
# MAGIC (Genie → your space → ⋯ → Delete). API-created spaces are removed with the
# MAGIC catalog's underlying tables.

# COMMAND ----------

banner("Teardown complete.")
dbutils.notebook.exit(f"99 complete · catalog '{CATALOG}' removed")
