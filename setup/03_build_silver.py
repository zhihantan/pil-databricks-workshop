# Databricks notebook source
# MAGIC %md
# MAGIC # 03 · Build Silver (cleaned, conformed, documented)
# MAGIC
# MAGIC Transforms messy Bronze into trustworthy Silver Delta tables:
# MAGIC
# MAGIC * **Clean:** fix mixed date formats, drop impossible values (negative dwell),
# MAGIC   coalesce blanks to `NULL`, trim strings, repair/flag bad port codes.
# MAGIC * **Deduplicate:** keep one row per natural key.
# MAGIC * **Conform:** consistent types, canonical column names.
# MAGIC * **Constrain:** `NOT NULL` + `CHECK` constraints, and PK/FK **informational**
# MAGIC   constraints so Genie and the optimizer understand join paths.
# MAGIC * **Document:** a comment on **every column** and every table (+ `domain`
# MAGIC   tag). Genie answer quality depends directly on these comments.
# MAGIC
# MAGIC Idempotent: tables are `CREATE OR REPLACE`; constraints are added if absent.

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

from pil_workshop import config
from pil_workshop.silver_build import (
    COLUMN_COMMENTS,
    add_constraints,
    apply_column_comments,
    build_all_silver,
)
from pil_workshop.utils import banner, ok, safe_identifier

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)

banner(f"03 · Building Silver in `{CATALOG}`.`{config.SILVER}`")

# COMMAND ----------

# MAGIC %md ### Clean + conform + write all Silver tables
# MAGIC The transformation SQL lives in `pil_workshop.silver_build` so it is unit-
# MAGIC testable and easy to patch. Each function reads from Bronze and returns the
# MAGIC cleaned Spark DataFrame; this cell writes them as Delta.

# COMMAND ----------

written = build_all_silver(spark, CATALOG)
for tbl, cnt in written:
    ok(f"silver.{tbl}: {cnt:,} rows")

# COMMAND ----------

# MAGIC %md ### Constraints (NOT NULL, CHECK, PK/FK informational)

# COMMAND ----------

add_constraints(spark, CATALOG)
ok("Constraints applied (NOT NULL / CHECK / PK / FK informational).")

# COMMAND ----------

# MAGIC %md ### Column & table comments + domain tags
# MAGIC Every column gets a business-meaningful comment — this is what makes Genie
# MAGIC answers accurate.

# COMMAND ----------

apply_column_comments(spark, CATALOG)
documented = sum(len(cols) for cols in COLUMN_COMMENTS.values())
ok(f"Applied {documented} column comments across {len(COLUMN_COMMENTS)} tables.")

# COMMAND ----------

# MAGIC %md ### Verify: no negative dwell, no dupes, FKs resolve

# COMMAND ----------

silver = f"`{CATALOG}`.`{config.SILVER}`"
neg = spark.sql(f"SELECT COUNT(*) c FROM {silver}.shipments WHERE dwell_hrs < 0").collect()[0]["c"]
dup = spark.sql(
    f"SELECT COUNT(*) c FROM (SELECT container_no, COUNT(*) n FROM {silver}.containers "
    f"GROUP BY container_no HAVING n > 1)"
).collect()[0]["c"]
orphan_bookings = spark.sql(f"""
    SELECT COUNT(*) c FROM {silver}.bookings b
    LEFT JOIN {silver}.customers c ON b.customer_id = c.customer_id
    WHERE c.customer_id IS NULL
""").collect()[0]["c"]

print(f"  negative dwell rows (want 0):        {neg}")
print(f"  duplicate container_no (want 0):     {dup}")
print(f"  orphan bookings→customers (want 0):  {orphan_bookings}")
assert neg == 0, "Silver still has negative dwell — cleaning failed."
assert dup == 0, "Silver still has duplicate containers — dedup failed."
ok("Silver quality checks passed.")

dbutils.notebook.exit(f"03 complete · {len(written)} silver tables")
