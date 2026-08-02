# Databricks notebook source
# MAGIC %md
# MAGIC # 08 · Agent Bricks — Invoice Extraction & Container Vision
# MAGIC
# MAGIC Two paths, both governed by Unity AI Gateway (endpoints from `pil_workshop.llm`):
# MAGIC
# MAGIC * **Primary (guided):** create an **Agent Bricks *Information Extraction*** agent
# MAGIC   over the invoice Volume in the UI (step-by-step below). Best when you want
# MAGIC   managed evals, versioning, and a no-code target schema.
# MAGIC * **Fallback (always works, runs here):** a SQL pipeline using
# MAGIC   `ai_parse_document` + `ai_query` with a JSON response schema →
# MAGIC   `silver.invoice_extractions`, reconciled against ground truth →
# MAGIC   `gold.invoice_exceptions`. Plus **container vision** via `ai_query` on the
# MAGIC   multimodal endpoint → `silver.container_inspections`, scored vs labels.
# MAGIC
# MAGIC > Every model call targets the same governed FMAPI endpoints as the app, so
# MAGIC > traffic appears on **Dashboard Page 4**.

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

from pil_workshop import agent_bricks, config, llm
from pil_workshop.utils import banner, ok, safe_identifier, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)

wc = WorkspaceClient()
endpoints = llm.resolve_endpoints(wc, region=config.REGION)
INVOICE_PATH = config.volume_path(CATALOG, config.VOLUME_INVOICES)
IMAGE_PATH = config.volume_path(CATALOG, config.VOLUME_IMAGES)

banner("08 · Agent Bricks + governed ai_query pipelines")
print(f"  Text endpoint (extraction):  {endpoints.text}")
print(f"  Vision endpoint (container): {endpoints.vision}")
for note in endpoints.notes:
    warn(note)

# COMMAND ----------

# MAGIC %md ### Primary path — Agent Bricks Information Extraction (UI walkthrough)

# COMMAND ----------

print(f"""
  Create an Agent Bricks Information Extraction agent (UI):
    1. Agents (left nav) → Agent Bricks → Information Extraction → Build.
    2. Source: the invoice Volume  {INVOICE_PATH}
    3. Define the output schema:
         invoice_no, date, customer, po_number, currency,
         line_items[] (description, amount), subtotal, tax, total, payment_terms
    4. Model: choose the governed FMAPI text endpoint  '{endpoints.text}'
       (so usage lands on the AI Gateway usage tables / Dashboard Page 4).
    5. Create → run on the sample → review the auto-generated eval.
    6. (Optional) Export the agent as an endpoint and point the app at it.

  When to use Agent Bricks vs. the hand-rolled ai_query pipeline below:
    • Agent Bricks: managed schema/eval/versioning, no-code iteration, built-in
      quality metrics — great for business users and repeatable extraction.
    • ai_query pipeline: full control in SQL/Python, easy to embed in DLT/jobs,
      transparent cost, no extra service — great for engineers and CI.
""")

# COMMAND ----------

# MAGIC %md ### Fallback path — invoice extraction pipeline (runs now)
# MAGIC If FMAPI endpoints aren't reachable in-region this cell will error clearly;
# MAGIC the notebook then explains what an account admin must enable.

# COMMAND ----------

extraction_sql = agent_bricks.build_invoice_extraction_sql(
    CATALOG, endpoints.text, INVOICE_PATH
)
try:
    spark.sql(extraction_sql)
    n = spark.table(f"`{CATALOG}`.`silver`.`invoice_extractions`").count()
    ok(f"Extracted {n} invoices → silver.invoice_extractions")
except Exception as exc:  # noqa: BLE001
    warn(f"Extraction pipeline failed: {exc}")
    print(
        "  If this is a model/region error: enable Foundation Model APIs and, "
        "for models not served in southeastasia, cross-geography routing "
        "(Account Console → Settings → Feature enablement)."
    )

# COMMAND ----------

# MAGIC %md ### Reconcile extractions → gold.invoice_exceptions

# COMMAND ----------

try:
    spark.sql(agent_bricks.build_invoice_reconciliation_sql(CATALOG))
    exc_df = spark.table(f"`{CATALOG}`.`gold`.`invoice_exceptions`")
    n_exc = exc_df.count()
    ok(f"Flagged {n_exc} invoice exceptions → gold.invoice_exceptions")
    display(exc_df.groupBy("exception_type").count().orderBy("exception_type"))
except Exception as exc:  # noqa: BLE001
    warn(f"Reconciliation skipped: {exc}")

# COMMAND ----------

# MAGIC %md ### Container vision classification (runs now)

# COMMAND ----------

vision_sql = agent_bricks.build_container_vision_sql(
    CATALOG, endpoints.vision, IMAGE_PATH
)
try:
    spark.sql(vision_sql)
    spark.sql(agent_bricks.build_vision_scored_sql(CATALOG))
    scored = spark.table(f"`{CATALOG}`.`silver`.`container_inspections_scored`")
    total = scored.count()
    correct = scored.filter("is_correct = 1").count()
    acc = 100.0 * correct / total if total else 0.0
    ok(f"Classified {total} containers · accuracy vs ground truth: {acc:.1f}%")
    banner("Container vision eval (confusion by ground-truth class)", char="-")
    display(
        scored.groupBy("gt_damage", "pred_damage").count()
        .orderBy("gt_damage", "pred_damage")
    )
except Exception as exc:  # noqa: BLE001
    warn(f"Vision pipeline failed: {exc}")
    print("  Ensure a multimodal FMAPI endpoint is available in-region "
          f"(chosen: {endpoints.vision}).")

# COMMAND ----------

dbutils.notebook.exit("08 complete · invoice extraction + container vision")
