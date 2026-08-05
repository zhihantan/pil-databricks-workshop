# Databricks notebook source
# MAGIC %md
# MAGIC # 05 · Create the AI/BI Dashboard
# MAGIC
# MAGIC Deploys the 4-page **PIL Operations** AI/BI (Lakeview) dashboard:
# MAGIC
# MAGIC 1. **Fleet & Network Ops** — reliability / utilization counters, delay trend,
# MAGIC    port scatter, worst-10 ports.
# MAGIC 2. **Commercial** — revenue-per-TEU trend, revenue by trade lane, D&D, top customers.
# MAGIC 3. **Sustainability** — fuel-efficiency trend, CO₂ by class, LNG vs VLSFO.
# MAGIC 4. **AI Usage & Governance** — tokens / requests / cost / errors from the Unity
# MAGIC    AI Gateway usage views (populates live during Part 2).
# MAGIC
# MAGIC The dashboard is built by `pil_workshop.dashboard_build` (same code that wrote
# MAGIC the committed `assets/dashboards/pil_operations.lvdash.json`) and deployed via
# MAGIC the Lakeview API wrapper in `pil_workshop.dbx_api`. Re-running updates in place.

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


REPO_ROOT = _add_repo_src_to_path()

import json

from databricks.sdk import WorkspaceClient

from pil_workshop import config, dashboard_build, dbx_api
from pil_workshop.utils import banner, ok, safe_identifier, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.text("dashboard_name", "PIL Operations", "Dashboard display name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
DASHBOARD_NAME = dbutils.widgets.get("dashboard_name") or "PIL Operations"

wc = WorkspaceClient()
banner(f"05 · Deploying dashboard '{DASHBOARD_NAME}' over `{CATALOG}`")

# COMMAND ----------

# MAGIC %md ### Build the serialized dashboard for this catalog

# COMMAND ----------

dashboard = dashboard_build.build_dashboard(CATALOG)
print(f"Pages: {[p['displayName'] for p in dashboard['pages']]}")
print(f"Datasets: {len(dashboard['datasets'])}")
serialized = json.dumps(dashboard)

# COMMAND ----------

# MAGIC %md ### Resolve a serverless SQL warehouse
# MAGIC The dashboard needs a warehouse to run its dataset queries.

# COMMAND ----------

warehouse_id = dbx_api.get_serverless_warehouse_id(wc)
if not warehouse_id:
    warn(
        "No serverless SQL warehouse found. Create one (SQL → Warehouses → "
        "Serverless) or set one up before the workshop. Attempting deploy anyway; "
        "the dashboard will need a warehouse assigned in the UI."
    )
    warehouse_id = ""
else:
    ok(f"Using serverless warehouse id: {warehouse_id}")

# COMMAND ----------

# MAGIC %md ### Deploy via the Lakeview API
# MAGIC Created directly in the current user's workspace **home** — alongside the
# MAGIC Genie spaces and MLflow experiments — rather than in a separate
# MAGIC `pil_workshop` subfolder. The API wrapper updates in place if a dashboard
# MAGIC with the same name exists.

# COMMAND ----------

me = wc.current_user.me()
parent_path = f"/Workspace/Users/{me.user_name}"
print(f"Dashboard parent folder: {parent_path}")

try:
    dash_id = dbx_api.create_or_update_lakeview_dashboard(
        display_name=DASHBOARD_NAME,
        serialized_dashboard=serialized,
        warehouse_id=warehouse_id,
        parent_path=parent_path,
        client=wc,
    )
    ok(f"Dashboard deployed (id={dash_id}).")
    try:
        host = spark.conf.get("spark.databricks.workspaceUrl")
        print(f"\n  Open: https://{host}/dashboardsv3/{dash_id}/published")
    except Exception:  # noqa: BLE001
        pass
    # Attach a daily refresh schedule (04:00 Asia/Singapore — after the 03:00
    # setup job rebuilds the gold layer). Idempotent + best-effort; needs a
    # warehouse and the Lakeview schedule API.
    if dash_id and warehouse_id:
        sched_id = dbx_api.ensure_lakeview_schedule(
            dash_id, warehouse_id, cron="0 0 4 * * ?",
            timezone="Asia/Singapore", display_name="Daily refresh", client=wc,
        )
        if sched_id:
            ok(f"Daily refresh scheduled (04:00 Asia/Singapore, id={sched_id}).")
        else:
            _serr = getattr(dbx_api.ensure_lakeview_schedule, "last_error", None)
            warn(f"Dashboard schedule not attached ({_serr or 'API unavailable'}); "
                 "add a daily refresh in the dashboard UI if desired.")
    elif dash_id and not warehouse_id:
        warn("No warehouse resolved — skipping the dashboard refresh schedule "
             "(a schedule needs a warehouse to run its queries).")
except Exception as exc:  # noqa: BLE001
    warn(f"Automated deploy failed: {exc}")
    print(
        "\nManual path: Dashboards → Create → ⋯ → Import, and select\n"
        "  assets/dashboards/pil_operations.lvdash.json\n"
        "then Find-and-replace the ${catalog} token with your catalog name."
    )
    dash_id = None

# COMMAND ----------

dbutils.notebook.exit(f"05 complete · dashboard={DASHBOARD_NAME} id={dash_id}")
