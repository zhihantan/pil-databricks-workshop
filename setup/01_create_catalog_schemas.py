# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Create Catalog, Schemas & Volumes
# MAGIC
# MAGIC Creates the Unity Catalog namespace for the PIL workshop, idempotently.
# MAGIC
# MAGIC | Object | Default |
# MAGIC |---|---|
# MAGIC | Catalog | `pil_workshop` |
# MAGIC | Schemas | `bronze`, `silver`, `gold`, `apps`, `ml` |
# MAGIC | Volumes (in `bronze`) | `raw_files`, `raw_invoices`, `container_images` |
# MAGIC
# MAGIC Re-running is safe (`CREATE ... IF NOT EXISTS`). Override the catalog / scale
# MAGIC via the widgets at the top.
# MAGIC
# MAGIC **Target environment:** Azure Databricks · `southeastasia` · serverless.

# COMMAND ----------

# MAGIC %md ### Bootstrap: put the shared `src/` library on the path
# MAGIC Every notebook starts with this cell so `import pil_workshop` works whether
# MAGIC the repo is opened via Git Folders/Repos or run from a bundle.

# COMMAND ----------

import os
import sys


def _add_repo_src_to_path() -> str:
    """Locate the repo root (the dir containing ``src/pil_workshop``) and add it."""
    here = os.getcwd()
    candidates = [here, os.path.dirname(here), "/Workspace" + here]
    # Walk upward a few levels to find src/pil_workshop.
    probe = here
    for _ in range(6):
        if os.path.isdir(os.path.join(probe, "src", "pil_workshop")):
            candidates.insert(0, probe)
            break
        probe = os.path.dirname(probe)
    for root in candidates:
        src = os.path.join(root, "src")
        if os.path.isdir(os.path.join(src, "pil_workshop")):
            if src not in sys.path:
                sys.path.insert(0, src)
            return root
    # Fallback: assume the notebook lives in <repo>/setup.
    src = os.path.abspath(os.path.join(here, "..", "src"))
    if src not in sys.path:
        sys.path.insert(0, src)
    return os.path.dirname(src)


REPO_ROOT = _add_repo_src_to_path()
print(f"Repo root: {REPO_ROOT}")

# COMMAND ----------

from pil_workshop import config
from pil_workshop.utils import banner, ok, safe_identifier, warn

# Widgets — the ONE place a user overrides catalog / scale.
dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.dropdown("scale", config.DEFAULT_SCALE, ["demo", "full"], "Data scale")

CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
SCALE = dbutils.widgets.get("scale") or config.DEFAULT_SCALE

banner(f"01 · Provisioning catalog '{CATALOG}' (scale={SCALE})")

# COMMAND ----------

# MAGIC %md ### Create the catalog
# MAGIC Requires the `CREATE CATALOG` privilege on the metastore (or `MANAGE` from a
# MAGIC metastore admin). If this fails, ask an account/metastore admin to grant it —
# MAGIC the error below says exactly what is missing.

# COMMAND ----------

try:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS `{CATALOG}`")
    spark.sql(
        f"COMMENT ON CATALOG `{CATALOG}` IS "
        f"'PIL Data + AI Workshop — synthetic container-liner data & AI assets.'"
    )
    ok(f"Catalog `{CATALOG}` ready")
except Exception as exc:  # noqa: BLE001
    raise RuntimeError(
        f"Could not create catalog '{CATALOG}'. You need the CREATE CATALOG "
        f"privilege on the metastore (Account Console → Catalog → Permissions), "
        f"or ask a metastore admin to pre-create it and grant you ALL PRIVILEGES.\n"
        f"Underlying error: {exc}"
    ) from exc

# COMMAND ----------

# MAGIC %md ### Create schemas and Bronze volumes

# COMMAND ----------

for schema in config.SCHEMAS:
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{schema}`")
    spark.sql(
        f"COMMENT ON SCHEMA `{CATALOG}`.`{schema}` IS "
        f"'{schema.title()} layer for the PIL workshop.'"
    )
    ok(f"Schema `{CATALOG}`.`{schema}`")

for volume in config.VOLUMES:
    spark.sql(f"CREATE VOLUME IF NOT EXISTS `{CATALOG}`.`{config.BRONZE}`.`{volume}`")
    ok(f"Volume `{CATALOG}`.`{config.BRONZE}`.`{volume}`")

# COMMAND ----------

# MAGIC %md ### Verify

# COMMAND ----------

schemas = [r.databaseName for r in spark.sql(f"SHOW SCHEMAS IN `{CATALOG}`").collect()]
missing = [s for s in config.SCHEMAS if s not in schemas]
if missing:
    warn(f"Missing schemas: {missing}")
else:
    ok(f"All schemas present: {', '.join(config.SCHEMAS)}")

print("\nWorkspace URL (open Catalog Explorer to inspect):")
try:
    host = spark.conf.get("spark.databricks.workspaceUrl")
    print(f"  https://{host}/explore/data/{CATALOG}")
except Exception:  # noqa: BLE001
    print(f"  Catalog: {CATALOG}")

dbutils.notebook.exit(f"01 complete · catalog={CATALOG} scale={SCALE}")
