# Databricks notebook source
# MAGIC %md
# MAGIC # 10 · Deploy the Databricks App
# MAGIC
# MAGIC Builds the frontend (if needed) and deploys the **PIL Invoice + Container
# MAGIC Vision** app from `/app`. The app runs as its **service principal** (no PATs);
# MAGIC grant it the resources listed in `app/app.yaml` (SQL warehouse, the governed
# MAGIC FMAPI text + vision endpoints with CAN QUERY, the Lakebase instance, and READ
# MAGIC VOLUME on the invoice/image Volumes) so its model traffic appears on
# MAGIC Dashboard Page 4.
# MAGIC
# MAGIC If the Apps API isn't available, the notebook prints the exact CLI/UI steps.

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

from databricks.sdk import WorkspaceClient

from pil_workshop import config, dbx_api, llm
from pil_workshop.utils import banner, ok, safe_identifier, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.text("app_name", "pil-invoice-vision", "Databricks App name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
APP_NAME = dbutils.widgets.get("app_name") or "pil-invoice-vision"

wc = WorkspaceClient()
# Same endpoints the app calls — used below to grant the app SP CAN QUERY.
resolved = llm.resolve_endpoints(wc, region=config.REGION)
banner(f"10 · Deploying app '{APP_NAME}'")

# COMMAND ----------

# MAGIC %md ### Confirm the frontend bundle exists
# MAGIC The app serves `app/backend/static`. Build it locally with
# MAGIC `cd app/frontend && npm install && npm run build`, then sync the repo, OR
# MAGIC build in CI. We check and warn (the app still starts with an API-only
# MAGIC placeholder page if the bundle is missing).

# COMMAND ----------

static_index = os.path.join(REPO_ROOT, "app", "backend", "static", "index.html")
if os.path.exists(static_index):
    ok("Frontend bundle present (app/backend/static/index.html).")
else:
    warn("Frontend bundle missing — build it: "
         "cd app/frontend && npm install && npm run build")

# COMMAND ----------

# MAGIC %md ### Determine the app source path in the workspace
# MAGIC Databricks Apps deploy from a workspace path. When this repo is cloned via
# MAGIC Git Folders, the app lives under the repo's `/app` folder in the workspace.

# COMMAND ----------

me = wc.current_user.me()
# Best-effort guess of the workspace repo path; override the widget if different.
default_src = f"/Workspace/Users/{me.user_name}/pil-databricks-workshop/app"
dbutils.widgets.text("app_source_path", default_src, "App source path (workspace)")
APP_SOURCE = dbutils.widgets.get("app_source_path") or default_src
print(f"App source path: {APP_SOURCE}")

# COMMAND ----------

# MAGIC %md ### Deploy

# COMMAND ----------

try:
    deployment = dbx_api.deploy_app(APP_NAME, APP_SOURCE, client=wc)
    if deployment is None:
        warn("Databricks Apps API not available in this workspace/SDK.")
        print(f"""
  Deploy via CLI instead:
    databricks apps create {APP_NAME}
    databricks sync ./app /Workspace/Users/{me.user_name}/{APP_NAME}
    databricks apps deploy {APP_NAME} \\
        --source-code-path /Workspace/Users/{me.user_name}/{APP_NAME}

  Then grant the app's service principal (see app/app.yaml):
    • CAN USE on the serverless SQL warehouse
    • CAN QUERY on the governed FMAPI text + vision endpoints
    • CAN CONNECT on the Lakebase instance 'pil-workshop-db'
    • READ VOLUME on {CATALOG}.bronze.raw_invoices and .container_images
""")
    else:
        ok(f"App '{APP_NAME}' deployment started.")
        try:
            host = spark.conf.get("spark.databricks.workspaceUrl")
            print(f"  Open: https://{host}/apps/{APP_NAME}")
        except Exception:  # noqa: BLE001
            pass
except Exception as exc:  # noqa: BLE001
    warn(f"Deploy failed: {exc}")

# COMMAND ----------

# MAGIC %md ### Grant the app's service principal everything it needs (automated)
# MAGIC A fresh customer run wires ALL the grants the app requires so it works with
# MAGIC no manual clicks — the same set this repo debugged live:
# MAGIC   * UC (via SQL): USE CATALOG, USE SCHEMA, SELECT, EXECUTE, READ VOLUME on
# MAGIC     the catalog + WRITE VOLUME on `bronze.raw_invoices` (upload target);
# MAGIC   * SQL warehouse: CAN USE (SDK); FMAPI text+vision endpoints: CAN QUERY (SDK).
# MAGIC The app's SP only exists after it is created above, so grants run here.

# COMMAND ----------

app_sp = dbx_api.app_service_principal_id(APP_NAME, client=wc)
if not app_sp:
    warn(f"App '{APP_NAME}' SP not resolvable yet; re-run this notebook once the "
         "app has finished provisioning to apply grants (the daily Job does this).")
else:
    ok(f"App service principal: {app_sp}")
    # 1) Unity Catalog grants (SQL). Catalog-scope read cascades to schemas/tables/
    #    volumes/functions; WRITE VOLUME is scoped to the two app upload targets
    #    (invoice PDFs + container images).
    uc_grants = [
        f"GRANT USE CATALOG, USE SCHEMA, SELECT, EXECUTE, READ VOLUME "
        f"ON CATALOG `{CATALOG}` TO `{app_sp}`",
        f"GRANT WRITE VOLUME ON VOLUME `{CATALOG}`.`bronze`.`raw_invoices` TO `{app_sp}`",
        f"GRANT WRITE VOLUME ON VOLUME `{CATALOG}`.`bronze`.`container_images` TO `{app_sp}`",
        # MODIFY on the app's own schema so the upload flows can INSERT into their
        # Delta sinks (invoice_extractions_app, container_inspections_app) on the
        # FIRST run too — notebook 08 also grants this, but 08 runs before the app
        # SP exists on a fresh setup, so granting here closes that ordering gap.
        f"GRANT MODIFY ON SCHEMA `{CATALOG}`.`apps` TO `{app_sp}`",
    ]
    for stmt in uc_grants:
        try:
            spark.sql(stmt)
            ok(f"  {stmt.split(' ON ')[0].replace('GRANT ', '')} ✓")
        except Exception as gexc:  # noqa: BLE001
            warn(f"  grant skipped ({stmt[:40]}…): {gexc}")
    # 2) Warehouse CAN USE + endpoint CAN QUERY (permission API, not SQL).
    try:
        wid = dbx_api.get_serverless_warehouse_id(client=wc)
    except Exception:  # noqa: BLE001
        wid = None
    endpoints_to_grant = sorted({resolved.text, resolved.vision})
    for line in dbx_api.grant_app_warehouse_and_endpoints(
        app_sp, wid, endpoints_to_grant, client=wc
    ):
        ok(f"  {line}")

# COMMAND ----------

# MAGIC %md ### Lakebase reminder
# MAGIC The one grant not automated here is **CAN CONNECT on the Lakebase instance**
# MAGIC (`pil-workshop-db`) — grant it in the Lakebase UI so the app's review queue /
# MAGIC decisions persist to Postgres. The app still runs (UC-only) without it.

# COMMAND ----------

dbutils.notebook.exit(f"10 complete · app={APP_NAME}")
