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

from pil_workshop import config, dbx_api
from pil_workshop.utils import banner, ok, safe_identifier, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.text("app_name", "pil-invoice-vision", "Databricks App name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
APP_NAME = dbutils.widgets.get("app_name") or "pil-invoice-vision"

wc = WorkspaceClient()
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

# MAGIC %md ### Reminder — resource grants
# MAGIC The app appears on Dashboard **Page 4** only once its service principal can
# MAGIC query the governed endpoints (so its tokens are logged alongside notebook
# MAGIC traffic). Grant the resources in `app/app.yaml` before the demo.

# COMMAND ----------

dbutils.notebook.exit(f"10 complete · app={APP_NAME}")
