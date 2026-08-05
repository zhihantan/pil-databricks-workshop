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
# Optional managed storage location for the catalog. Leave BLANK on workspaces
# whose metastore has a storage root (the common case). Set it only if catalog
# creation fails with "Metastore storage root URL does not exist" / Default
# Storage — e.g. an external-location path like
# 'abfss://<container>@<account>.dfs.core.windows.net/<path>' that you have a
# storage credential for. See the error guidance below.
dbutils.widgets.text("managed_location", "", "Catalog MANAGED LOCATION (optional)")

CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
SCALE = dbutils.widgets.get("scale") or config.DEFAULT_SCALE
MANAGED_LOCATION = (dbutils.widgets.get("managed_location") or "").strip()

banner(f"01 · Provisioning catalog '{CATALOG}' (scale={SCALE})")

# COMMAND ----------

# MAGIC %md ### Create the catalog
# MAGIC Requires the `CREATE CATALOG` privilege on the metastore (or `MANAGE` from a
# MAGIC metastore admin).
# MAGIC
# MAGIC **Azure "Default Storage" workspaces:** some Azure metastores have **no
# MAGIC storage root**, so a plain `CREATE CATALOG` fails with *"Metastore storage
# MAGIC root URL does not exist"*. This is **not** a privilege problem. Fix it with
# MAGIC **either** of:
# MAGIC 1. **Pre-create the catalog in the UI** using Default Storage (Catalog
# MAGIC    Explorer → Create catalog), then re-run — this notebook detects the
# MAGIC    existing catalog and continues; **or**
# MAGIC 2. Set the **`managed_location`** widget to an external-location path you
# MAGIC    have a storage credential for (e.g.
# MAGIC    `abfss://<container>@<account>.dfs.core.windows.net/<path>`) and re-run —
# MAGIC    the notebook then creates the catalog `WITH MANAGED LOCATION`.

# COMMAND ----------


def _catalog_exists(name: str) -> bool:
    try:
        return any(
            r[0] == name for r in spark.sql("SHOW CATALOGS").collect()
        )
    except Exception:  # noqa: BLE001
        return False


def _needs_storage_location(msg: str) -> bool:
    """True when the failure is the Azure Default-Storage / no-storage-root case
    (fixable with a MANAGED LOCATION), not a privilege problem."""
    m = msg.lower()
    return (
        "storage root url does not exist" in m
        or "default storage is enabled" in m
        or "provide a storage location" in m
        or "managed location" in m
    )


def _is_privilege_error(msg: str) -> bool:
    m = msg.lower()
    return (
        "permission" in m
        or "privilege" in m
        or "does not have" in m
        or "not authorized" in m
        or "access denied" in m
    )


def _comment_catalog() -> None:
    try:
        spark.sql(
            f"COMMENT ON CATALOG `{CATALOG}` IS "
            f"'PIL Data + AI Workshop — synthetic container-liner data & AI assets.'"
        )
    except Exception as cexc:  # noqa: BLE001 - comment is cosmetic; never fatal
        warn(f"Could not set catalog comment (non-fatal): {cexc}")


def _provision_catalog() -> None:
    # 0) Already there (e.g. pre-created in the UI with Default Storage)? Done.
    if _catalog_exists(CATALOG):
        ok(f"Catalog `{CATALOG}` already exists — using it.")
        _comment_catalog()
        return

    # 1) If a managed location was supplied, use it directly (Default-Storage
    #    workspaces need this; harmless elsewhere if you have the credential).
    if MANAGED_LOCATION:
        try:
            spark.sql(
                f"CREATE CATALOG IF NOT EXISTS `{CATALOG}` "
                f"MANAGED LOCATION '{MANAGED_LOCATION}'"
            )
            ok(f"Catalog `{CATALOG}` ready (MANAGED LOCATION supplied).")
            _comment_catalog()
            return
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Could not create catalog '{CATALOG}' with MANAGED LOCATION "
                f"'{MANAGED_LOCATION}'. Verify the path is a valid external "
                f"location you have a storage credential + CREATE MANAGED STORAGE "
                f"on.\nUnderlying error: {exc}"
            ) from exc

    # 2) Default path: plain create (works when the metastore has a storage root).
    try:
        spark.sql(f"CREATE CATALOG IF NOT EXISTS `{CATALOG}`")
        ok(f"Catalog `{CATALOG}` ready")
        _comment_catalog()
        return
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        # Race / eventual-consistency: it may have been created concurrently.
        if _catalog_exists(CATALOG):
            ok(f"Catalog `{CATALOG}` now present — continuing.")
            _comment_catalog()
            return
        if _needs_storage_location(msg):
            raise RuntimeError(
                f"Could not create catalog '{CATALOG}': this Azure workspace's "
                f"metastore has no storage root (Default Storage). This is NOT a "
                f"privilege problem. Do ONE of the following, then re-run this "
                f"notebook (it will detect the catalog and continue):\n"
                f"  (A) Create the catalog once in the UI using Default Storage: "
                f"Catalog Explorer → Create catalog → name it '{CATALOG}'; or\n"
                f"  (B) Re-run with the 'managed_location' widget set to an "
                f"external-location path you have a storage credential for, e.g. "
                f"abfss://<container>@<account>.dfs.core.windows.net/{CATALOG} — "
                f"the notebook will then CREATE CATALOG ... WITH MANAGED LOCATION.\n"
                f"Underlying error: {msg}"
            ) from exc
        if _is_privilege_error(msg):
            raise RuntimeError(
                f"Could not create catalog '{CATALOG}'. You need the CREATE "
                f"CATALOG privilege on the metastore (Account Console → Catalog → "
                f"Permissions), or ask a metastore admin to pre-create it and "
                f"grant you ALL PRIVILEGES.\nUnderlying error: {msg}"
            ) from exc
        raise RuntimeError(
            f"Could not create catalog '{CATALOG}'. If your Azure account uses "
            f"Default Storage, either pre-create '{CATALOG}' in the UI or set the "
            f"'managed_location' widget (see this cell's notes). Otherwise verify "
            f"you have CREATE CATALOG on the metastore.\nUnderlying error: {msg}"
        ) from exc


_provision_catalog()

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
