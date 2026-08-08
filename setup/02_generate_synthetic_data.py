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
# incremental: on re-runs, APPEND a fresh slice to the event/transaction tables
# (grows the dataset) instead of overwriting them. "auto" = append when a base
# load already exists, full-overwrite on the first run. "off" = always overwrite.
dbutils.widgets.dropdown("incremental", "auto", ["auto", "off"], "Incremental append mode")
# Size of each incremental slice, in DAYS of activity (the base is ~24 months).
# Default = config.DEFAULT_INCREMENT_DAYS (30) ≈ ~8k bookings + ~62k events per
# run at full scale, date-confined to the recent window so the latest dates grow
# visibly each run. The Data Setup job passes this through; lower it for a
# lighter cadence.
dbutils.widgets.text(
    "increment_days", str(config.DEFAULT_INCREMENT_DAYS), "Incremental slice size (days)")

CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
SCALE = dbutils.widgets.get("scale") or config.DEFAULT_SCALE
INCREMENTAL = dbutils.widgets.get("incremental") or "auto"
try:
    INCREMENT_DAYS = float(
        dbutils.widgets.get("increment_days") or config.DEFAULT_INCREMENT_DAYS)
except ValueError:
    INCREMENT_DAYS = config.DEFAULT_INCREMENT_DAYS
BRONZE = f"`{CATALOG}`.`{config.BRONZE}`"
RAW_PATH = config.volume_path(CATALOG, config.VOLUME_RAW)

banner(f"02 · Generating synthetic data (scale={SCALE}) → {BRONZE}")

# COMMAND ----------

# MAGIC %md ### Generate the coherent dataset in-memory (deterministic)

# COMMAND ----------

# Decide append-vs-overwrite. In "auto" we append when a base load already
# exists (the bookings table is present + non-empty) — i.e. every run after the
# first. The id offset is taken past the current MAX across the incremental
# tables so appended PKs never collide with prior runs.
def _table_exists(fqn: str) -> bool:
    try:
        parts = fqn.replace("`", "").split(".")
        return spark.sql(
            f"SHOW TABLES IN `{parts[0]}`.`{parts[1]}` LIKE '{parts[2]}'"
        ).count() > 0
    except Exception:  # noqa: BLE001
        return False


APPEND = False
ID_OFFSET = 0
if INCREMENTAL == "auto" and _table_exists(f"{BRONZE}.bookings"):
    try:
        _pk = {"bookings": "booking_id", "shipments": "shipment_id",
               "container_events": "event_id", "port_calls": "port_call_id",
               "invoices": "invoice_id", "invoice_line_items": "line_item_id"}
        maxes = []
        for tbl, pk in _pk.items():
            if _table_exists(f"{BRONZE}.{tbl}"):
                m = spark.sql(f"SELECT MAX(CAST(`{pk}` AS BIGINT)) m FROM {BRONZE}.{tbl}").collect()[0]["m"]
                maxes.append(int(m or 0))
        ID_OFFSET = (max(maxes) if maxes else 0) + 1
        APPEND = ID_OFFSET > 1
    except Exception as exc:  # noqa: BLE001
        print(f"Could not read existing max ids ({exc}); doing a full overwrite.")
        APPEND = False

t0 = datetime.now()
spec = config.get_scale(SCALE)
if APPEND:
    banner(f"Incremental run — appending ~{INCREMENT_DAYS}-day slice "
           f"(id_offset={ID_OFFSET:,})", char="-")
    # Dimensions are regenerated (identical, deterministic) and overwritten so
    # nothing drifts; the event/txn tables get a small NEW day-sized batch
    # anchored at 'now' (not a full re-load).
    data = datagen.generate_all(spec)                 # base (for dimension overwrite)
    increment = datagen.generate_increment(
        spec, id_offset=ID_OFFSET, days=INCREMENT_DAYS, today=datetime.now().date())
else:
    data = datagen.generate_all(spec)
    increment = None
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


# On a fresh/overwrite run, write every table with overwrite. On an incremental
# run, dimensions still overwrite (kept in lockstep, no drift) but the
# event/transaction tables APPEND their fresh offset batch — so the dataset
# grows each run while FKs stay valid against the fixed dimensions.
created = []
inc_tables = set(datagen.INCREMENTAL_TABLES)
for name, rows in data.items():
    do_append = APPEND and name in inc_tables
    src_rows = increment.get(name, []) if do_append else rows
    df = _to_bronze_df(name, src_rows)
    df = messiness.inject(name, df)  # deliberate Bronze quality issues
    target = f"{BRONZE}.{name}"
    writer = df.write.format("delta")
    if do_append:
        # schema already established by the base load; append the new slice.
        writer.mode("append").saveAsTable(target)
        action = "appended"
    else:
        writer.mode("overwrite").option("overwriteSchema", "true").saveAsTable(target)
        action = "overwrote"
    spark.sql(
        f"COMMENT ON TABLE {target} IS "
        f"'Bronze (raw, intentionally messy) — {name} for PIL workshop.'"
    )
    total = spark.table(target).count()
    created.append((name, total))
    ok(f"Bronze table {target} ({action}; now {total:,} rows)")

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
