# Databricks notebook source
# MAGIC %md
# MAGIC # 00 · One-Click Setup — PIL Data + AI Workshop
# MAGIC
# MAGIC Provisions the whole workshop (phases 1–5). By default this notebook creates
# MAGIC **two serverless Databricks Jobs** (Workflows):
# MAGIC
# MAGIC * **PIL Workshop — Data Setup** (recurring, every 12h) — the data + assets
# MAGIC   pipeline: 01, 01b, 02, 03, 04, 07, 08, 11, 12. Each run refreshes the
# MAGIC   medallion and appends an incremental data slice.
# MAGIC * **PIL Workshop — Consumables Setup** (one-time, unscheduled) — the deploy/
# MAGIC   serve-once surfaces: 05 dashboard, 06 Genie, 09 Lakebase, 10 app. Reads the
# MAGIC   gold layer, so run it **once after Data Setup** has completed.
# MAGIC
# MAGIC **Target:** Azure Databricks · `southeastasia` · serverless.
# MAGIC
# MAGIC | Widget | Purpose |
# MAGIC |---|---|
# MAGIC | `catalog` | Target UC catalog (default `pil_workshop`). |
# MAGIC | `scale` | `demo` (fast) or `full` (master-prompt volumes). |
# MAGIC | `orchestration` | `job` (create both Jobs — default) or `inline` (run in-process here). |
# MAGIC | `run_now` | For `job` mode: trigger the Data Setup run immediately after creating the Jobs. |
# MAGIC | `schedule_cron` / `timezone` | Data Setup schedule (Quartz cron; default 12-hourly). |
# MAGIC | `skip_steps` | (inline mode) comma-separated prefixes to skip, e.g. `11,12`. |
# MAGIC | `continue_on_error` | (inline mode) keep going when a step fails. |
# MAGIC
# MAGIC Re-running is **no-op-safe**: each Job is reset-in-place by name; every notebook
# MAGIC is idempotent. To remove everything, run `99_teardown`.

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

from pil_workshop import config, job_builder, preflight
from pil_workshop.utils import banner, fail, ok, safe_identifier, summary_table, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.dropdown("scale", config.DEFAULT_SCALE, ["demo", "full"], "Data scale")
dbutils.widgets.dropdown("orchestration", "job", ["job", "inline"], "Orchestration mode")
dbutils.widgets.dropdown("run_now", "true", ["true", "false"], "Run the Job now (job mode)")
dbutils.widgets.text("schedule_cron", job_builder.DEFAULT_CRON, "Data Setup schedule (Quartz cron; default 12-hourly)")
dbutils.widgets.text("timezone", job_builder.DEFAULT_TIMEZONE, "Schedule timezone")
dbutils.widgets.text("skip_steps", "", "Skip steps (inline mode, e.g. 11,12)")
dbutils.widgets.dropdown("continue_on_error", "false", ["true", "false"], "Continue on error (inline)")
# Optional: only needed on Azure "Default Storage" metastores that have no
# storage root, where a plain CREATE CATALOG fails. Set to an external-location
# path you have a storage credential for; passed to notebook 01 only.
dbutils.widgets.text("managed_location", "", "Catalog MANAGED LOCATION (optional, Default-Storage metastores)")

CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
SCALE = dbutils.widgets.get("scale") or config.DEFAULT_SCALE
MODE = dbutils.widgets.get("orchestration") or "job"
RUN_NOW = dbutils.widgets.get("run_now") == "true"
CRON = dbutils.widgets.get("schedule_cron") or job_builder.DEFAULT_CRON
TZ = dbutils.widgets.get("timezone") or job_builder.DEFAULT_TIMEZONE
SKIP = {s.strip() for s in dbutils.widgets.get("skip_steps").split(",") if s.strip()}
CONTINUE = dbutils.widgets.get("continue_on_error") == "true"
MANAGED_LOCATION = (dbutils.widgets.get("managed_location") or "").strip()

wc = WorkspaceClient()
banner("PIL Data + AI Workshop — One-Click Setup")
print(f"  Catalog: {CATALOG} · Scale: {SCALE} · Region: {config.REGION} · Mode: {MODE}")

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

# MAGIC %md ## Resolve this notebook's workspace folder
# MAGIC The Job's task notebook paths are workspace paths, so we need the folder this
# MAGIC notebook lives in (its parent's `setup/`).

# COMMAND ----------


def _workspace_setup_dir() -> str:
    """Return the workspace path of the `setup/` folder holding these notebooks."""
    try:
        ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
        nb_path = ctx.notebookPath().get()  # e.g. /Users/me/repo/setup/00_setup_all
        return nb_path.rsplit("/", 1)[0]
    except Exception as exc:  # noqa: BLE001
        warn(f"Could not resolve notebook path via context ({exc}); "
             "falling back to a Repos-style guess.")
        me = wc.current_user.me().user_name
        return f"/Workspace/Users/{me}/pil-databricks-workshop/setup"


def _host() -> str:
    try:
        return "https://" + spark.conf.get("spark.databricks.workspaceUrl")
    except Exception:  # noqa: BLE001
        return wc.config.host

# COMMAND ----------

# MAGIC %md ## Resolve a serverless SQL warehouse (for dashboard/Genie tasks)

# COMMAND ----------

from pil_workshop import dbx_api

WAREHOUSE_ID = dbx_api.get_serverless_warehouse_id(wc) or None
if WAREHOUSE_ID:
    ok(f"Serverless warehouse: {WAREHOUSE_ID}")
else:
    warn("No serverless warehouse found; dashboard/Genie tasks will need one assigned.")

# COMMAND ----------

# MAGIC %md ## Orchestrate

# COMMAND ----------

if MODE == "job":
    banner("Creating the two Databricks Jobs")
    setup_dir = _workspace_setup_dir()
    print(f"  Notebook root: {setup_dir}")

    # Job 1 — recurring Data Setup (scheduled every 12h). Refreshes the medallion
    # + assets and appends an incremental data slice each run.
    print(f"  Data Setup schedule: cron='{CRON}' tz='{TZ}'")
    data_job_id = job_builder.create_or_update_data_job(
        wc, setup_dir, catalog=CATALOG, scale=SCALE,
        warehouse_id=WAREHOUSE_ID, managed_location=MANAGED_LOCATION or None,
        timezone=TZ, cron=CRON, paused=False,
    )
    # Prove the schedule is live (unpaused + running on its cron), self-healing
    # if it was ever manually paused between setup runs.
    _live = job_builder.ensure_schedule_unpaused(wc, data_job_id)
    ok(f"Data Setup job ready: {job_builder.job_url(_host(), data_job_id)}")
    print(f"  Schedule: cron='{CRON}' tz='{TZ}' — "
          f"{'UNPAUSED (live, running on schedule)' if _live else 'NO SCHEDULE'}")

    # Job 2 — one-time Consumables Setup (unscheduled): dashboard, Genie, Lakebase,
    # app. It reads the gold layer, so run it once AFTER Data Setup completes.
    consumables_job_id = job_builder.create_or_update_consumables_job(
        wc, setup_dir, catalog=CATALOG, scale=SCALE, warehouse_id=WAREHOUSE_ID,
        timezone=TZ,
    )
    ok(f"Consumables Setup job ready (unscheduled): "
       f"{job_builder.job_url(_host(), consumables_job_id)}")

    run_id = None
    if RUN_NOW:
        run = wc.jobs.run_now(job_id=data_job_id)
        run_id = run.run_id
        ok(f"Triggered Data Setup run: {_host()}/jobs/{data_job_id}/runs/{run_id}")
        print("  Watch under Workflows → Jobs. When it finishes, run the "
              "**Consumables Setup** job once to deploy the dashboard, Genie "
              "space, Lakebase, and app.")
    else:
        print("\n  Next: run **Data Setup** first, then **Consumables Setup** "
              "once (it consumes the gold layer Data Setup produces).")

    try:
        dbutils.jobs.taskValues.set(key="data_job_id", value=str(data_job_id))
        dbutils.jobs.taskValues.set(key="consumables_job_id", value=str(consumables_job_id))
    except Exception:  # noqa: BLE001
        pass

    dbutils.notebook.exit(
        f"Jobs created — Data Setup ({data_job_id}, 12-hourly) + "
        f"Consumables Setup ({consumables_job_id}, one-time); "
        f"run_now={RUN_NOW}" + (f" data_run_id={run_id}" if run_id else "")
    )

# COMMAND ----------

# MAGIC %md ## Inline mode (run steps in-process here)
# MAGIC Kept for quick single-session runs. Uses `dbutils.notebook.run` per step.

# COMMAND ----------

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
if MANAGED_LOCATION:
    # Only notebook 01 reads managed_location; harmless to pass to all in inline
    # mode since the others ignore unknown args.
    BASE_ARGS["managed_location"] = MANAGED_LOCATION
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

# MAGIC %md ### Inline summary + created assets

# COMMAND ----------

banner("Setup summary")
print(summary_table(results, ["step", "notebook", "status", "seconds", "message"]))
total_s = sum(r["seconds"] for r in results)
n_ok = sum(1 for r in results if r["status"] == "OK")
print(f"\n  {n_ok}/{len(results)} steps OK · total {total_s:.1f}s")

host = _host()
assets = [
    {"asset": "Catalog", "name": CATALOG, "url": f"{host}/explore/data/{CATALOG}"},
    {"asset": "Dashboard", "name": "PIL Operations", "url": f"{host}/dashboardsv3"},
    {"asset": "Genie", "name": "PIL Shipping Operations", "url": f"{host}/genie"},
    {"asset": "App", "name": "pil-invoice-vision", "url": f"{host}/apps"},
]
print(summary_table(assets, ["asset", "name", "url"]))

failed = [r for r in results if r["status"] == "FAILED"]
dbutils.notebook.exit(
    f"Inline setup {'OK' if not failed else 'completed with failures'} · "
    f"{n_ok}/{len(results)} steps · {total_s:.1f}s"
)
