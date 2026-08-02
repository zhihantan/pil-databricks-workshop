# Databricks notebook source
# MAGIC %md
# MAGIC # 07 · Generate Unstructured Data (Invoice PDFs + Container Images)
# MAGIC
# MAGIC Produces the raw unstructured inputs for the Agent Bricks module:
# MAGIC
# MAGIC * **Freight-invoice PDFs** (reportlab) — 4 templates, multi-currency, ~10%
# MAGIC   with deliberate anomalies (wrong total / missing PO / duplicate number) →
# MAGIC   `/Volumes/{catalog}/bronze/raw_invoices/`.
# MAGIC * **Labeled container images** (Pillow) — clean + damaged (dents, rust, door
# MAGIC   misalignment), with a ground-truth labels table →
# MAGIC   `/Volumes/{catalog}/bronze/container_images/`.
# MAGIC
# MAGIC Ground truth is written to `silver.invoice_pdf_ground_truth` and
# MAGIC `silver.container_image_labels` for the reconciliation / accuracy evals in 08.
# MAGIC Deterministic (seed=42); re-running overwrites cleanly.

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

# COMMAND ----------

# MAGIC %md ### Install PDF/image libraries
# MAGIC reportlab / Pillow may not be preinstalled on serverless — install on demand,
# MAGIC then restart Python so the new packages are importable.

# COMMAND ----------

# MAGIC %pip install reportlab Pillow

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

import os
import sys

# Re-add path after Python restart.
here = os.getcwd()
probe = here
for _ in range(6):
    if os.path.isdir(os.path.join(probe, "src", "pil_workshop")):
        if os.path.join(probe, "src") not in sys.path:
            sys.path.insert(0, os.path.join(probe, "src"))
        break
    probe = os.path.dirname(probe)

from pil_workshop import config
from pil_workshop.config import SEED
from pil_workshop.datagen import unstructured
from pil_workshop.utils import banner, ok, safe_identifier

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.dropdown("scale", config.DEFAULT_SCALE, ["demo", "full"], "Data scale")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
SCALE = dbutils.widgets.get("scale") or config.DEFAULT_SCALE
spec = config.get_scale(SCALE)

INVOICE_DIR = "/" + config.volume_path(CATALOG, config.VOLUME_INVOICES).lstrip("/")
IMAGE_DIR = "/" + config.volume_path(CATALOG, config.VOLUME_IMAGES).lstrip("/")

banner(f"07 · Generating {spec.invoice_pdfs} invoices + "
       f"{spec.container_images} images (scale={SCALE})")

# COMMAND ----------

# MAGIC %md ### Generate invoice PDFs

# COMMAND ----------

os.makedirs(INVOICE_DIR, exist_ok=True)
invoice_gt = unstructured.generate_invoice_pdfs(INVOICE_DIR, spec.invoice_pdfs, seed=SEED)
ok(f"Wrote {len(invoice_gt)} invoice PDFs → {INVOICE_DIR}")
n_anom = sum(1 for r in invoice_gt if r["gt_anomaly"])
print(f"  Deliberate anomalies: {n_anom} "
      f"({', '.join(sorted({r['gt_anomaly'] for r in invoice_gt if r['gt_anomaly']}))})")

# COMMAND ----------

# MAGIC %md ### Generate container images

# COMMAND ----------

os.makedirs(IMAGE_DIR, exist_ok=True)
image_gt = unstructured.generate_container_images(IMAGE_DIR, spec.container_images, seed=SEED)
ok(f"Wrote {len(image_gt)} container images → {IMAGE_DIR}")
from collections import Counter

dist = Counter(r["gt_damage"] for r in image_gt)
print(f"  Damage distribution: {dict(dist)}")

# COMMAND ----------

# MAGIC %md ### Persist ground truth for evals

# COMMAND ----------

silver = f"`{CATALOG}`.`{config.SILVER}`"
(spark.createDataFrame(invoice_gt).write.format("delta").mode("overwrite")
 .option("overwriteSchema", "true").saveAsTable(f"{silver}.invoice_pdf_ground_truth"))
(spark.createDataFrame(image_gt).write.format("delta").mode("overwrite")
 .option("overwriteSchema", "true").saveAsTable(f"{silver}.container_image_labels"))
ok("Ground-truth tables: invoice_pdf_ground_truth, container_image_labels")

# COMMAND ----------

# MAGIC %md ### Verify files are readable as a Volume listing

# COMMAND ----------

pdfs = [f.name for f in dbutils.fs.ls(config.volume_path(CATALOG, config.VOLUME_INVOICES))
        if f.name.endswith(".pdf")]
imgs = [f.name for f in dbutils.fs.ls(config.volume_path(CATALOG, config.VOLUME_IMAGES))
        if f.name.endswith(".png")]
print(f"  Volume invoices: {len(pdfs)} PDFs")
print(f"  Volume images:   {len(imgs)} PNGs")

dbutils.notebook.exit(
    f"07 complete · {len(invoice_gt)} invoices · {len(image_gt)} images"
)
