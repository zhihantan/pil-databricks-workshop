# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Create the Genie Space (Genie Agent)
# MAGIC
# MAGIC Creates a **Genie space** over the gold layer + metric views using
# MAGIC `assets/genie/space_config.yml` — curated tables, business instructions,
# MAGIC 5 benchmark-verified common questions, and 6 verified benchmark answers.
# MAGIC
# MAGIC Genie space creation via API is newer/preview in some regions. This notebook
# MAGIC tries the SDK; if unavailable in **`southeastasia`**, it prints exact click-path
# MAGIC UI instructions and writes the resolved config so a facilitator can paste it in.
# MAGIC
# MAGIC > Genie answer quality depends on the column comments applied in notebook 03 and
# MAGIC > the metric-view definitions from notebook 04 — run those first.

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


REPO_ROOT = _add_repo_src_to_path()

import yaml
from databricks.sdk import WorkspaceClient

from pil_workshop import config, dbx_api
from pil_workshop.utils import banner, ok, safe_identifier, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)

wc = WorkspaceClient()
banner(f"06 · Creating Genie space over `{CATALOG}`.gold")

# COMMAND ----------

# MAGIC %md ### Load and resolve the space config

# COMMAND ----------

config_path = os.path.join(REPO_ROOT, "assets", "genie", "space_config.yml")
with open(config_path) as fh:
    raw = fh.read().replace("${catalog}", CATALOG)
space = yaml.safe_load(raw)

requested_tables = space["tables"]


def _table_exists(fqn: str) -> bool:
    """True if a three-level `cat`.`schema`.`obj` exists (view or table)."""
    try:
        # strip backticks, split on the last two dots
        parts = fqn.replace("`", "").split(".")
        cat, sch, obj = parts[0], parts[1], ".".join(parts[2:])
        rows = spark.sql(
            f"SHOW TABLES IN `{cat}`.`{sch}` LIKE '{obj}'"
        ).collect()
        return len(rows) > 0
    except Exception:  # noqa: BLE001
        return False


# Filter to tables that actually exist now. The finance/inventory/repositioning
# analytics views (v_*) are built by notebooks 04/11/12; on a fresh run 06 runs
# before 11/12, so those two bind on the daily re-run once they're populated.
tables = [t for t in requested_tables if _table_exists(t)]
skipped = [t for t in requested_tables if t not in tables]
print(f"Title: {space['title']}")
print(f"Curated tables ({len(tables)} of {len(requested_tables)} present):")
for t in tables:
    print(f"  • {t}")
if skipped:
    print(f"Deferred (not built yet — will bind on the next run): {len(skipped)}")
    for t in skipped:
        print(f"  – {t}")
print(f"Text instructions: {len(space.get('text_instructions', ''))} chars")
print(f"Example SQL (trusted assets): {len(space.get('example_sqls', []))}")
print(f"Sample questions: {len(space.get('sample_questions', []))}")
print(f"Benchmarks: {len(space.get('benchmarks', []))}")

# COMMAND ----------

# MAGIC %md ### Resolve a serverless warehouse

# COMMAND ----------

warehouse_id = dbx_api.get_serverless_warehouse_id(wc)
if warehouse_id:
    ok(f"Using serverless warehouse id: {warehouse_id}")
else:
    warn("No serverless warehouse found — Genie needs one assigned.")
    warehouse_id = ""

# COMMAND ----------

# MAGIC %md ### Try to create the space via SDK

# COMMAND ----------

# Idempotency: if a space with this title already exists, reuse it rather than
# creating duplicates on every run.
space_id = None
try:
    _resp = wc.genie.list_spaces()
    # SDK returns a response object with a `.spaces` list (not directly iterable).
    _spaces = getattr(_resp, "spaces", None) or _resp
    for s in _spaces:
        if getattr(s, "title", None) == space["title"]:
            space_id = getattr(s, "space_id", None)
            break
except Exception as exc:  # noqa: BLE001
    warn(f"Could not list existing Genie spaces (will create new): {exc}")

if space_id:
    ok(f"Genie space already exists (id={space_id}); re-syncing config.")
    # Re-sync so newly-built views (e.g. the inventory/repositioning analytics
    # views from notebooks 11/12) and any instruction changes are picked up on
    # re-runs, instead of leaving the reused space stale.
    synced = dbx_api.update_genie_space(
        space_id=space_id,
        title=space["title"],
        warehouse_id=warehouse_id,
        table_identifiers=tables,
        instructions=space.get("text_instructions", ""),
        sample_questions=space.get("sample_questions", []),
        client=wc,
        description=space.get("description"),
        example_sqls=space.get("example_sqls"),
        benchmarks=space.get("benchmarks"),
    )
    if synced:
        ok(f"Re-synced space with {len(tables)} tables + instructions.")
    else:
        _upd_err = getattr(dbx_api.update_genie_space, "last_error", None)
        warn(f"Could not re-sync existing space ({_upd_err}); tables may be "
             "stale — edit the space in the UI or recreate it if needed.")
else:
    space_id = dbx_api.create_genie_space(
        title=space["title"],
        warehouse_id=warehouse_id,
        table_identifiers=tables,
        instructions=space.get("text_instructions", ""),
        sample_questions=space.get("sample_questions", []),
        client=wc,
        description=space.get("description"),
        example_sqls=space.get("example_sqls"),
        benchmarks=space.get("benchmarks"),
    )
    _genie_err = getattr(dbx_api.create_genie_space, "last_error", None)
    if _genie_err:
        warn(f"Genie create error: {_genie_err}")

if space_id:
    ok(f"Genie space ready (id={space_id}).")
    try:
        host = spark.conf.get("spark.databricks.workspaceUrl")
        print(f"  Open: https://{host}/genie/rooms/{space_id}")
    except Exception:  # noqa: BLE001
        pass
    print("  Fully configured via API — no UI steps needed. Bound: tables, "
          "warehouse, instructions, example SQL, benchmarks, and these "
          "benchmark-VERIFIED suggested questions:")
    for _q in space.get("sample_questions", []):
        print(f"    • {_q}")
else:
    warn("Programmatic Genie creation unavailable here — use the UI (below).")

# COMMAND ----------

# MAGIC %md ### UI fallback (always documented)

# COMMAND ----------

print(f"""
  Create the Genie space in the UI:
    1. Genie (left nav) → New → Genie space.
    2. Connect the serverless SQL warehouse.
    3. Add these tables (gold + metric views only):
{chr(10).join('         - ' + t for t in tables)}
    4. Paste the Instructions from assets/genie/space_config.yml.
    5. Pin these {len(space['sample_questions'])} suggested questions (each is
       benchmark-VERIFIED — Genie is scored GOOD on them):
{chr(10).join('         - ' + q for q in space['sample_questions'])}
    6. Under 'Benchmarks', add the {len(space['benchmarks'])} verified Q&A pairs
       (question + expected SQL) to enable the evaluation demo.

  Genie One (business-user chat): once the space is live, business users can ask
  the same questions from the Genie One surface, pin answers to the dashboard,
  and invoke the app — covered in the participant guide.
""")

# Write the resolved config next to the notebook for easy copy/paste.
resolved_out = f"/Workspace/Users/{wc.current_user.me().user_name}/pil_workshop_genie_resolved.yml"
try:
    dbutils.fs.put("file:" + resolved_out, raw, overwrite=True)
except Exception:  # noqa: BLE001
    pass

_err = getattr(dbx_api.create_genie_space, "last_error", None)
dbutils.notebook.exit(
    f"06 complete · genie_space_id={space_id}"
    + (f" · err={_err}" if _err and not space_id else "")
)
