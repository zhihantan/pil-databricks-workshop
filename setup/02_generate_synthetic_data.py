# Databricks notebook source
# MAGIC %md
# MAGIC # 02 · Generate Synthetic Shipping Data (Bronze)
# MAGIC
# MAGIC Uses the deterministic generators in `pil_workshop.datagen` (seed = 42) to
# MAGIC produce a coherent ~24-month history of PIL's container-liner business, lands
# MAGIC the raw records as JSON/CSV in the `raw_files` Volume, then registers **Bronze
# MAGIC Delta tables** with *deliberate messiness* (nulls, dupes, mixed date formats,
# MAGIC bad port codes, negative dwell) so Silver has real cleaning work to show.
# MAGIC
# MAGIC Re-running is safe: tables use `CREATE OR REPLACE`, generation is deterministic.

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

import json
from datetime import datetime

from pil_workshop import config, datagen
from pil_workshop.datagen import messiness
from pil_workshop.utils import banner, ok, safe_identifier

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.dropdown("scale", config.DEFAULT_SCALE, ["demo", "full"], "Data scale")

CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
SCALE = dbutils.widgets.get("scale") or config.DEFAULT_SCALE
BRONZE = f"`{CATALOG}`.`{config.BRONZE}`"
RAW_PATH = config.volume_path(CATALOG, config.VOLUME_RAW)

banner(f"02 · Generating synthetic data (scale={SCALE}) → {BRONZE}")

# COMMAND ----------

# MAGIC %md ### Generate the coherent dataset in-memory (deterministic)

# COMMAND ----------

t0 = datetime.now()
spec = config.get_scale(SCALE)
data = datagen.generate_all(spec)
gen_secs = (datetime.now() - t0).total_seconds()

print(f"Generated in {gen_secs:.1f}s:")
for name, rows in data.counts().items():
    print(f"  {name:28s} {rows:>10,}")

summary = datagen.summarize(data)
print(
    f"\nApprox schedule reliability: "
    f"{summary['approx_schedule_reliability_pct']}%  "
    f"(expect {config.KPI_RANGES['schedule_reliability_pct']})"
)
print(f"Anomalous invoices (ground truth): {summary['n_anomalous_invoices']}")

# COMMAND ----------

# MAGIC %md ### Land raw files in the Volume
# MAGIC Big event-style datasets are written as newline-delimited JSON; reference
# MAGIC data as JSON too. This gives participants real files to `read_files()` /
# MAGIC Auto Load from, exactly like a customer landing zone.

# COMMAND ----------


def _write_raw(name: str, rows: list[dict]) -> str:
    """Write a list of dicts as newline-delimited JSON into the raw Volume."""
    folder = f"{RAW_PATH}/{name}"
    dbutils.fs.mkdirs(folder)
    path = f"{folder}/{name}.jsonl"
    # Write via the driver local FS proxy for Volumes.
    local = "/" + path.lstrip("/")
    with open(local, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str) + "\n")
    return path


for name, rows in data.items():
    p = _write_raw(name, rows)
ok(f"Raw files written under {RAW_PATH}")

# COMMAND ----------

# MAGIC %md ### Register Bronze Delta tables (with deliberate messiness)
# MAGIC We build Spark DataFrames from the generated rows, inject realistic quality
# MAGIC issues into a copy destined for Bronze, and write Delta tables. Array columns
# MAGIC (e.g. route rotations) are JSON-encoded so Bronze stays file-like.

# COMMAND ----------


def _to_bronze_df(name: str, rows: list[dict]):
    """Build a Spark DataFrame, JSON-encoding nested list/dict fields."""
    if not rows:
        return spark.createDataFrame([], "placeholder STRING")
    norm = []
    for r in rows:
        rr = {}
        for k, v in r.items():
            rr[k] = json.dumps(v) if isinstance(v, (list, dict)) else v
        norm.append(rr)
    return spark.createDataFrame(norm)


created = []
for name, rows in data.items():
    df = _to_bronze_df(name, rows)
    df = messiness.inject(name, df)  # deliberate Bronze quality issues
    target = f"{BRONZE}.{name}"
    (
        df.write.format("delta")
        .mode("overwrite")
        .option("overwriteSchema", "true")
        .saveAsTable(target)
    )
    spark.sql(
        f"COMMENT ON TABLE {target} IS "
        f"'Bronze (raw, intentionally messy) — {name} for PIL workshop.'"
    )
    created.append((name, df.count()))
    ok(f"Bronze table {target}")

# COMMAND ----------

# MAGIC %md ### Verify row counts

# COMMAND ----------

print(f"{'table':30s}{'rows':>12s}")
for name, cnt in created:
    print(f"  {name:28s}{cnt:>12,}")

# Quick messiness proof-point: show some nulls / dupes exist in bronze.
dup_voy = spark.sql(
    f"SELECT COUNT(*) c FROM (SELECT container_no, COUNT(*) n "
    f"FROM {BRONZE}.containers GROUP BY container_no HAVING n > 1)"
).collect()[0]["c"]
print(f"\nDuplicate container_no groups in bronze (intentional): {dup_voy}")

dbutils.notebook.exit(f"02 complete · {len(created)} bronze tables · scale={SCALE}")
