# Databricks notebook source
# MAGIC %md
# MAGIC # 04 · Build Gold (tables, materialized views, metric views)
# MAGIC
# MAGIC Creates the business-facing Gold layer PIL cares about:
# MAGIC
# MAGIC * **Base views** — `_rev_base`, `_sustainability_base` (sources for metric views).
# MAGIC * **Materialized views** — `mv_daily_operations_kpis`, `mv_port_performance`,
# MAGIC   `mv_customer_revenue`, `mv_container_utilization` (daily `SCHEDULE`; falls back
# MAGIC   to plain views if MVs are unavailable).
# MAGIC * **Metric views** — created from `assets/metric_views/*.yml` via
# MAGIC   `CREATE VIEW ... WITH METRICS LANGUAGE YAML`: schedule reliability, delivery/
# MAGIC   transit, utilization, dwell/turnaround, revenue, sustainability, working capital.
# MAGIC
# MAGIC Ends with **KPI smoke tests**: prints every headline KPI and asserts each lands
# MAGIC in a plausible industry range so facilitators can trust the numbers on screen.

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

from pil_workshop import config, gold_build
from pil_workshop.utils import banner, fail, ok, safe_identifier, summary_table, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)

banner(f"04 · Building Gold in `{CATALOG}`.`{config.GOLD}`")

# COMMAND ----------

# MAGIC %md ### Base views (metric-view sources)

# COMMAND ----------

gold_build.create_base_views(spark, CATALOG)
ok("Base views: _rev_base, _sustainability_base")

# COMMAND ----------

# MAGIC %md ### Materialized views

# COMMAND ----------

mv_results = gold_build.create_materialized_views(spark, CATALOG)
for name, kind in mv_results:
    ok(f"{name} ({kind})")

# COMMAND ----------

# MAGIC %md ### Metric views (from YAML)

# COMMAND ----------

metric_results = gold_build.create_metric_views(spark, CATALOG)
for name, status in metric_results:
    (ok if status == "created" else warn)(f"{name}: {status}")

# COMMAND ----------

# MAGIC %md ### Analytics views (Genie Code dashboard pages L2–L4)
# MAGIC Governed views backing the build-your-own-dashboard prompts. The finance
# MAGIC view builds here (its source `silver.invoices` exists); the inventory and
# MAGIC repositioning views depend on the ML outputs and are (re)created at the end
# MAGIC of notebooks 11 and 12. Re-running this cell after 11/12 builds all three.

# COMMAND ----------

analytics_views = gold_build.create_analytics_views(spark, CATALOG)
for name in analytics_views:
    ok(f"analytics view: {name}")
missing_av = [v for v in ("v_financial_health", "v_inventory_planning",
                          "v_repositioning_summary") if v not in analytics_views]
if missing_av:
    warn(f"Deferred until ML notebooks run: {', '.join(missing_av)} "
         "(they read gold.demand_forecasts / gold.repositioning_plan).")

# COMMAND ----------

# MAGIC %md ### KPI smoke tests
# MAGIC Compute the headline KPIs and check each is in a plausible industry range.

# COMMAND ----------

kpis = gold_build.compute_kpi_summary(spark, CATALOG)
checks = gold_build.check_kpis(kpis)

banner("PIL KPI Summary (sanity-check these live)", char="-")
print(summary_table(checks, ["kpi", "value", "expected", "status"]))

out_of_range = [c for c in checks if c["status"] != "PASS"]
if out_of_range:
    for c in out_of_range:
        fail(f"{c['kpi']} = {c['value']} outside {c['expected']}")
    warn(
        "Some KPIs are out of range. Data still loads; review the generator "
        "calibration in src/pil_workshop/datagen if this is unexpected."
    )
else:
    ok("All KPIs are within plausible industry ranges. ✅")

# COMMAND ----------

# MAGIC %md ### Verify metric views answer a query
# MAGIC A quick MEASURE() query proves the metric views are usable by Genie/dashboards.

# COMMAND ----------

gold = f"`{CATALOG}`.`{config.GOLD}`"
try:
    df = spark.sql(f"""
        SELECT MEASURE(`Schedule Reliability %`) AS reliability
        FROM {gold}.metric_schedule_reliability
    """)
    val = df.collect()[0]["reliability"]
    ok(f"metric_schedule_reliability answers: Schedule Reliability % = {val}")
except Exception as exc:  # noqa: BLE001
    warn(f"Metric-view MEASURE() query failed (region/preview?): {exc}")

n_mv = sum(1 for _, k in mv_results if k == "mv")
dbutils.notebook.exit(
    f"04 complete · {len(mv_results)} MVs ({n_mv} materialized) · "
    f"{len(metric_results)} metric views · KPIs {'PASS' if not out_of_range else 'CHECK'}"
)
