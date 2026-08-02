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

# MAGIC %md ### Invoice extraction — ai_parse_document + ai_extract + ai_query
# MAGIC This is the **programmatic Information-Extraction path** and it runs now.
# MAGIC A live bake-off on these invoices (scored vs. ground truth) picked the tool
# MAGIC for each job:
# MAGIC
# MAGIC | Function | Role | Why |
# MAGIC |---|---|---|
# MAGIC | `ai_parse_document` | PDF → text | the document-parsing step |
# MAGIC | `ai_extract` | flat header fields | matched `ai_query` accuracy, ~25% faster, clean struct |
# MAGIC | `ai_query` | nested `line_items[]`, subtotal, tax, total | the schema `ai_extract` can't express |
# MAGIC
# MAGIC Both AI calls hit the governed FMAPI endpoint (`{endpoints.text}`), so usage
# MAGIC lands on Dashboard Page 4.
# MAGIC
# MAGIC **Agent Bricks IE as the no-code alternative** (UI): Agents → Agent Bricks →
# MAGIC Information Extraction → Build over `{INVOICE_PATH}` with the same schema.
# MAGIC Databricks currently exposes no API to create/invoke an IE agent (it's
# MAGIC UI-created and consumed via batch `ai_query`), so this SQL pipeline is the
# MAGIC shippable programmatic equivalent.

# COMMAND ----------

# MAGIC %md #### Step 1 — parse PDFs to text (ai_parse_document)

# COMMAND ----------

try:
    spark.sql(agent_bricks.build_invoice_parse_sql(CATALOG, INVOICE_PATH))
    n_parsed = spark.table(f"`{CATALOG}`.`silver`.`invoice_parsed_text`").count()
    ok(f"Parsed {n_parsed} invoice PDFs → silver.invoice_parsed_text")
except Exception as exc:  # noqa: BLE001
    warn(f"ai_parse_document step failed: {exc}")

# COMMAND ----------

# MAGIC %md #### Step 2 — extract fields (ai_extract flat + ai_query nested)

# COMMAND ----------

try:
    spark.sql(agent_bricks.build_invoice_extraction_sql(
        CATALOG, endpoints.text, INVOICE_PATH))
    n = spark.table(f"`{CATALOG}`.`silver`.`invoice_extractions`").count()
    ok(f"Extracted {n} invoices → silver.invoice_extractions "
       "(flat fields via ai_extract, line-items via ai_query)")
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

# Vision runs through the Python `llm.chat` path (OpenAI-style multimodal
# messages) — the same governed call the app backend uses. This is more robust
# than SQL `ai_query(files => ...)`, whose image-arg typing varies by runtime.
IMAGE_LOCAL = "/" + IMAGE_PATH.lstrip("/")
try:
    # List images via dbutils.fs (reliable on Volumes) rather than glob, which is
    # unreliable on the FUSE mount in some serverless contexts.
    img_files = [f.name for f in dbutils.fs.ls(IMAGE_PATH) if f.name.endswith(".png")]
    print(f"  Found {len(img_files)} images under {IMAGE_PATH}")
    rows = agent_bricks.classify_container_images(
        IMAGE_LOCAL, endpoints.vision, llm, file_names=img_files
    )
    if not rows:
        raise RuntimeError(
            f"No inspection rows produced (found {len(img_files)} image files at "
            f"{IMAGE_LOCAL}). Check Volume readability and the vision endpoint "
            f"'{endpoints.vision}'."
        )
    # Use an explicit schema: inference fails ([CANNOT_DETERMINE_TYPE]) when a
    # column is None across all rows (e.g. a null confidence from the model).
    inspection_schema = (
        "file_name STRING, damage STRING, damage_type STRING, "
        "confidence DOUBLE, recommended_action STRING"
    )
    norm_rows = [
        {
            "file_name": r.get("file_name"),
            "damage": r.get("damage"),
            "damage_type": r.get("damage_type"),
            "confidence": float(r["confidence"]) if r.get("confidence") is not None else None,
            "recommended_action": r.get("recommended_action"),
        }
        for r in rows
    ]
    (
        spark.createDataFrame(norm_rows, schema=inspection_schema)
        .write.format("delta").mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(f"`{CATALOG}`.`silver`.`container_inspections`")
    )
    ok(f"Classified {len(rows)} containers → silver.container_inspections")
    spark.sql(agent_bricks.build_vision_scored_sql(CATALOG))
    scored = spark.table(f"`{CATALOG}`.`silver`.`container_inspections_scored`")
    total = scored.count()
    correct = scored.filter("is_correct = 1").count()
    acc = 100.0 * correct / total if total else 0.0
    ok(f"Accuracy vs ground truth: {acc:.1f}% ({correct}/{total})")
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
