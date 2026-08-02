# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · One-Click Setup — PIL Data + AI Workshop
# MAGIC
# MAGIC Runs the entire workshop setup (phases 1–5) in order, with a **preflight
# MAGIC check**, per-step timing, and a final summary of every asset created with its
# MAGIC workspace URL.
# MAGIC
# MAGIC **Target:** Azure Databricks · `southeastasia` · serverless.
# MAGIC
# MAGIC | Widget | Purpose |
# MAGIC |---|---|
# MAGIC | `catalog` | Target UC catalog (default `pil_workshop`). |
# MAGIC | `scale` | `demo` (fast) or `full` (master-prompt volumes). |
# MAGIC | `skip_steps` | Comma-separated notebook prefixes to skip, e.g. `11,12`. |
# MAGIC | `continue_on_error` | If `true`, keep going when a step fails. |
# MAGIC
# MAGIC Re-running is **no-op-safe** (idempotent). To remove everything, run `99_teardown`.

# COMMAND ----------

import os
import sys
import time


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
SETUP_DIR = os.path.join(REPO_ROOT, "setup")

from databricks.sdk import WorkspaceClient

from pil_workshop import config, preflight
from pil_workshop.utils import banner, fail, ok, safe_identifier, summary_table, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.dropdown("scale", config.DEFAULT_SCALE, ["demo", "full"], "Data scale")
dbutils.widgets.text("skip_steps", "", "Skip steps (e.g. 11,12)")
dbutils.widgets.dropdown("continue_on_error", "false", ["true", "false"], "Continue on error")

CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
SCALE = dbutils.widgets.get("scale") or config.DEFAULT_SCALE
SKIP = {s.strip() for s in dbutils.widgets.get("skip_steps").split(",") if s.strip()}
CONTINUE = dbutils.widgets.get("continue_on_error") == "true"

wc = WorkspaceClient()
banner("PIL Data + AI Workshop — One-Click Setup")
print(f"  Catalog: {CATALOG} · Scale: {SCALE} · Region: {config.REGION}")
if SKIP:
    print(f"  Skipping steps: {sorted(SKIP)}")

# COMMAND ----------

# MAGIC %md ## Preflight checks
# MAGIC Verifies privileges and platform capabilities. Hard **FAIL**s stop the run
# MAGIC (unless `continue_on_error=true`); WARN/SKIP are informational.

# COMMAND ----------

checks = preflight.run_all(spark, wc, CATALOG, config.REGION)
print(summary_table(
    [{"check": c.name, "status": c.status, "detail": c.detail} for c in checks],
    ["check", "status", "detail"]))

blockers = preflight.blocking_failures(checks)
if blockers and not CONTINUE:
    banner("Preflight FAILED — resolve the blockers above, or set "
           "continue_on_error=true to proceed anyway.", char="!")
    for b in blockers:
        fail(f"{b.name}: {b.detail}")
    dbutils.notebook.exit("Preflight failed — see blockers above.")
else:
    ok("Preflight complete.")

# COMMAND ----------

# MAGIC %md ## Run the pipeline
# MAGIC Each step is a child notebook run via `dbutils.notebook.run`, passing the
# MAGIC catalog/scale widgets through. Timing and exit messages are collected.

# COMMAND ----------

# (step-prefix, notebook, base-timeout-seconds). Prefix matches skip_steps.
STEPS = [
    ("01", "01_create_catalog_schemas", 600),
    ("01b", "01b_ai_gateway_setup", 600),
    ("02", "02_generate_synthetic_data", 1800),
    ("03", "03_build_silver", 1200),
    ("04", "04_build_gold", 1200),
    ("05", "05_create_dashboard", 600),
    ("06", "06_create_genie_space", 600),
    ("07", "07_generate_invoices_and_images", 1200),
    ("08", "08_agent_bricks_setup", 1800),
    ("09", "09_lakebase_setup", 900),
    ("10", "10_deploy_app", 900),
    ("11", "11_ml_forecasting", 1800),
    ("12", "12_ml_route_optimization", 900),
]

BASE_ARGS = {"catalog": CATALOG, "scale": SCALE}
results = []
for prefix, notebook, timeout in STEPS:
    if prefix in SKIP:
        results.append({"step": prefix, "notebook": notebook,
                        "status": "SKIPPED", "seconds": 0, "message": "skip_steps"})
        warn(f"[{prefix}] skipped")
        continue
    path = os.path.join(SETUP_DIR, notebook)
    banner(f"[{prefix}] {notebook}", char="-")
    t0 = time.time()
    try:
        msg = dbutils.notebook.run(path, timeout, BASE_ARGS)
        elapsed = time.time() - t0
        results.append({"step": prefix, "notebook": notebook, "status": "OK",
                        "seconds": round(elapsed, 1), "message": (msg or "")[:60]})
        ok(f"[{prefix}] done in {elapsed:.1f}s — {msg}")
    except Exception as exc:  # noqa: BLE001
        elapsed = time.time() - t0
        results.append({"step": prefix, "notebook": notebook, "status": "FAILED",
                        "seconds": round(elapsed, 1), "message": str(exc)[:60]})
        fail(f"[{prefix}] FAILED after {elapsed:.1f}s: {exc}")
        if not CONTINUE:
            banner("Stopping (continue_on_error=false).", char="!")
            break

# COMMAND ----------

# MAGIC %md ## Summary — steps

# COMMAND ----------

banner("Setup summary")
print(summary_table(results, ["step", "notebook", "status", "seconds", "message"]))
total_s = sum(r["seconds"] for r in results)
n_ok = sum(1 for r in results if r["status"] == "OK")
print(f"\n  {n_ok}/{len(results)} steps OK · total {total_s:.1f}s")

# COMMAND ----------

# MAGIC %md ## Summary — created assets & URLs

# COMMAND ----------

try:
    host = spark.conf.get("spark.databricks.workspaceUrl")
except Exception:  # noqa: BLE001
    host = "<workspace-host>"

assets = [
    {"asset": "Catalog", "name": CATALOG,
     "url": f"https://{host}/explore/data/{CATALOG}"},
    {"asset": "Gold KPIs (MV)", "name": "mv_daily_operations_kpis",
     "url": f"https://{host}/explore/data/{CATALOG}/gold/mv_daily_operations_kpis"},
    {"asset": "Dashboard", "name": "PIL Operations",
     "url": f"https://{host}/dashboardsv3"},
    {"asset": "Genie", "name": "PIL Shipping Operations",
     "url": f"https://{host}/genie"},
    {"asset": "App", "name": "pil-invoice-vision",
     "url": f"https://{host}/apps"},
    {"asset": "Forecasts", "name": "gold.demand_forecasts",
     "url": f"https://{host}/explore/data/{CATALOG}/gold/demand_forecasts"},
    {"asset": "Repositioning", "name": "gold.repositioning_plan",
     "url": f"https://{host}/explore/data/{CATALOG}/gold/repositioning_plan"},
]
print(summary_table(assets, ["asset", "name", "url"]))

banner("Next: open the dashboard & Genie (Part 1), then the app (Part 2).")
print("  Facilitator guide: docs/facilitator_guide.md")
print("  Participant guide: docs/participant_guide.md")

failed = [r for r in results if r["status"] == "FAILED"]
dbutils.notebook.exit(
    f"Setup {'OK' if not failed else 'completed with failures'} · "
    f"{n_ok}/{len(results)} steps · {total_s:.1f}s"
)
