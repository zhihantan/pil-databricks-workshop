# Databricks notebook source
# MAGIC %md
# MAGIC # 06 · Create the Genie Space (Genie Agent)
# MAGIC
# MAGIC Creates a **Genie space** over the gold layer + metric views using
# MAGIC `assets/genie/space_config.yml` — curated tables, business instructions,
# MAGIC ~15 sample questions, and 5 benchmark/verified answers for the evaluation demo.
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

tables = space["tables"]
print(f"Title: {space['title']}")
print(f"Curated tables ({len(tables)}):")
for t in tables:
    print(f"  • {t}")
print(f"Sample questions: {len(space['sample_questions'])}")
print(f"Benchmarks: {len(space['benchmarks'])}")

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
    for s in wc.genie.list_spaces():
        if getattr(s, "title", None) == space["title"]:
            space_id = getattr(s, "space_id", None)
            break
except Exception:  # noqa: BLE001
    pass

if space_id:
    ok(f"Genie space already exists (id={space_id}); reusing.")
else:
    space_id = dbx_api.create_genie_space(
        title=space["title"],
        warehouse_id=warehouse_id,
        table_identifiers=tables,
        instructions=space["instructions"],
        sample_questions=space["sample_questions"],
        client=wc,
        description=space.get("description"),
    )

if space_id:
    ok(f"Genie space ready (id={space_id}).")
    try:
        host = spark.conf.get("spark.databricks.workspaceUrl")
        print(f"  Open: https://{host}/genie/rooms/{space_id}")
    except Exception:  # noqa: BLE001
        pass
    print("  NOTE: tables are bound via API; add the Instructions and sample "
          "questions from space_config.yml in the Genie UI (not expressible in "
          "the current serialized-space API).")
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
    5. Add the {len(space['sample_questions'])} sample questions.
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

dbutils.notebook.exit(f"06 complete · genie_space_id={space_id}")
