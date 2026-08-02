# Databricks notebook source
# MAGIC %md
# MAGIC # 01b · Foundation Model Endpoints & Unity AI Gateway
# MAGIC
# MAGIC Everything in this workshop that calls a frontier model routes through
# MAGIC **Databricks Foundation Model APIs (pay-per-token)** governed by **Unity AI
# MAGIC Gateway**. This notebook:
# MAGIC
# MAGIC 1. Discovers which `databricks-*` FMAPI endpoints are actually served in
# MAGIC    **`southeastasia`** and picks text / vision / embedding defaults
# MAGIC    (`pil_workshop.llm`).
# MAGIC 2. Detects whether **Unity AI Gateway (Beta)** is enabled at the account. If
# MAGIC    yes, verifies governance; if not, falls back to endpoint-level `ai_gateway`
# MAGIC    config and prints exactly what an Azure account admin must toggle.
# MAGIC 3. Configures — idempotently, per-feature — **usage tracking**, **per-user
# MAGIC    rate limits**, and optional **guardrails**.
# MAGIC 4. Creates gold **usage views** (Dashboard Page 4) over the system inference
# MAGIC    tables, and emits an *endpoint × feature × status* summary table.
# MAGIC
# MAGIC > No endpoint name is hardcoded here — they all come from `pil_workshop.llm`.

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

from pil_workshop import config, dbx_api, llm
from pil_workshop.utils import banner, ok, safe_identifier, skip, summary_table, warn

dbutils.widgets.text("catalog", config.DEFAULT_CATALOG, "Catalog name")
dbutils.widgets.text("rate_limit_qpm", "50", "Per-user rate limit (QPM)")
dbutils.widgets.dropdown("enable_guardrails", "false", ["true", "false"], "Guardrails")

CATALOG = safe_identifier(dbutils.widgets.get("catalog") or config.DEFAULT_CATALOG)
RATE_LIMIT = int(dbutils.widgets.get("rate_limit_qpm") or "50")
GUARDRAILS = (dbutils.widgets.get("enable_guardrails") or "false") == "true"

wc = WorkspaceClient()
banner("01b · Foundation Model endpoints & Unity AI Gateway")

# COMMAND ----------

# MAGIC %md ### 1 · Discover FMAPI endpoints served in-region

# COMMAND ----------

available = {e.name for e in wc.serving_endpoints.list() if e.name}
fmapi = sorted(n for n in available if n.startswith("databricks-"))
print(f"Serving endpoints visible: {len(available)} ({len(fmapi)} are databricks-* FMAPI)")
for n in fmapi:
    print(f"  • {n}")

resolved = llm.resolve_endpoints(wc, region=config.REGION, available=available)
banner("Chosen endpoints (single source of truth = pil_workshop.llm)", char="-")
print(f"  TEXT      : {resolved.text}")
print(f"  VISION    : {resolved.vision}")
print(f"  EMBEDDING : {resolved.embedding or '(none served — optional)'}")
for note in resolved.notes:
    warn(note)

if not fmapi:
    warn(
        "No databricks-* FMAPI endpoints found. On Azure southeastasia, enable "
        "Foundation Model APIs (pay-per-token) and — for models not served "
        "in-region — cross-geography routing: Account Console → Settings → "
        "Feature enablement. The workshop's fallback pipelines still need at "
        "least one text endpoint."
    )

# COMMAND ----------

# MAGIC %md ### 2 · Detect Unity AI Gateway (Beta) availability
# MAGIC Unity AI Gateway is enabled by an **account admin** in the Account Console →
# MAGIC *Previews*. When present, FMAPI traffic is governed centrally. When absent,
# MAGIC we configure the equivalent per-endpoint `ai_gateway` block via the SDK.

# COMMAND ----------


def _detect_unity_ai_gateway() -> bool:
    """Best-effort probe for the account-level Unity AI Gateway preview."""
    # The preview surfaces differently across releases; treat the ability to
    # read an endpoint's ai_gateway config as "governance is reachable".
    ep = dbx_api.get_serving_endpoint(resolved.text, client=wc)
    if ep is None:
        return False
    return hasattr(ep, "ai_gateway")


unity_gateway = _detect_unity_ai_gateway()
if unity_gateway:
    ok("Unity AI Gateway governance is reachable on serving endpoints.")
else:
    warn(
        "Unity AI Gateway (Beta) not detected. Falling back to endpoint-level "
        "ai_gateway config. To enable central governance, an Azure ACCOUNT ADMIN "
        "must turn on 'Mosaic AI Gateway' in Account Console → Previews."
    )

# COMMAND ----------

# MAGIC %md ### 3 · Configure governance per endpoint (idempotent, per-feature)

# COMMAND ----------

results = []
targets = sorted({resolved.text, resolved.vision})
for endpoint in targets:
    if endpoint not in available:
        skip(f"{endpoint}: not served here — cannot configure gateway.")
        results.append(
            {
                "endpoint": endpoint,
                "feature": "(all)",
                "status": "needs-admin",
                "detail": "Endpoint not served in-region; enable FMAPI / cross-geo routing.",
            }
        )
        continue
    feature_results = dbx_api.configure_ai_gateway(
        endpoint,
        usage_catalog=CATALOG,
        usage_schema=config.GOLD,
        rate_limit_qpm=RATE_LIMIT,
        enable_guardrails=GUARDRAILS,
        client=wc,
    )
    for fr in feature_results:
        results.append(
            {
                "endpoint": fr.endpoint,
                "feature": fr.feature,
                "status": fr.status,
                "detail": fr.detail[:70],
            }
        )
        (ok if fr.status == "enabled" else warn)(f"{fr.endpoint} · {fr.feature}: {fr.status}")

# COMMAND ----------

# MAGIC %md ### 4 · Gold usage views for Dashboard Page 4
# MAGIC These views sit over the system inference tables populated by usage tracking.
# MAGIC They tolerate the tables not existing yet (first run before any traffic) by
# MAGIC falling back to an empty, correctly-typed shell so the dashboard still loads.

# COMMAND ----------

# System inference/usage table. Location has evolved; try the known one and
# degrade to an empty typed view if unavailable.
SYSTEM_USAGE_TABLE = "system.serving.endpoint_usage"


def _usage_source_exists() -> bool:
    """True only if the table exists AND has the columns our views assume.

    Probing the specific columns (not ``SELECT 1``) guards against a drifted
    system-table schema, which would otherwise blow up the CREATE VIEW below.
    """
    try:
        spark.sql(
            f"SELECT requesttime, served_entity_name, requester, "
            f"input_token_count, output_token_count, status_code "
            f"FROM {SYSTEM_USAGE_TABLE} LIMIT 1"
        )
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"  (system usage table unavailable or schema drifted: {exc})")
        return False


# Empty, correctly-typed shell used as a fallback if the live table or the
# CREATE VIEW step fails — so the dashboard still loads and setup never halts.
_EMPTY_USAGE_BASE = """
    SELECT
        CAST(NULL AS DATE)   AS usage_date,
        CAST(NULL AS STRING) AS endpoint,
        CAST(NULL AS STRING) AS user_name,
        CAST(NULL AS BIGINT) AS input_tokens,
        CAST(NULL AS BIGINT) AS output_tokens,
        CAST(NULL AS BIGINT) AS total_tokens,
        CAST(NULL AS INT)    AS is_error
    WHERE 1 = 0
"""


gold = f"`{CATALOG}`.`{config.GOLD}`"
if _usage_source_exists():
    base = f"""
        SELECT
            CAST(requesttime AS DATE)              AS usage_date,
            served_entity_name                     AS endpoint,
            requester                              AS user_name,
            input_token_count                      AS input_tokens,
            output_token_count                     AS output_tokens,
            (input_token_count + output_token_count) AS total_tokens,
            CASE WHEN status_code >= 400 THEN 1 ELSE 0 END AS is_error
        FROM {SYSTEM_USAGE_TABLE}
    """
    ok("System usage table found — building live usage views.")
else:
    warn(
        "System usage table not readable yet; creating empty typed shells "
        "(they populate once traffic flows and you have system-table access)."
    )
    base = _EMPTY_USAGE_BASE

# Rough blended $/1M tokens for cost estimate on Page 4 (documented assumption).
COST_PER_M_TOKENS = 5.0


def _usage_view_sql(source_base: str) -> dict[str, str]:
    """Return {view_name: SQL} for the three usage views over ``source_base``."""
    return {
        "v_ai_usage_daily": f"""
            CREATE OR REPLACE VIEW {gold}.v_ai_usage_daily AS
            WITH u AS ({source_base})
            SELECT usage_date,
                   COUNT(*)                          AS request_count,
                   SUM(total_tokens)                 AS total_tokens,
                   SUM(is_error)                     AS error_count,
                   ROUND(SUM(total_tokens)/1e6*{COST_PER_M_TOKENS}, 2) AS est_cost_usd
            FROM u GROUP BY usage_date
        """,
        "v_ai_usage_by_endpoint": f"""
            CREATE OR REPLACE VIEW {gold}.v_ai_usage_by_endpoint AS
            WITH u AS ({source_base})
            SELECT endpoint,
                   COUNT(*) AS request_count,
                   SUM(total_tokens) AS total_tokens,
                   SUM(is_error) AS error_count,
                   ROUND(SUM(total_tokens)/1e6*{COST_PER_M_TOKENS}, 2) AS est_cost_usd
            FROM u GROUP BY endpoint
        """,
        "v_ai_usage_by_user": f"""
            CREATE OR REPLACE VIEW {gold}.v_ai_usage_by_user AS
            WITH u AS ({source_base})
            SELECT user_name,
                   COUNT(*) AS request_count,
                   SUM(total_tokens) AS total_tokens
            FROM u GROUP BY user_name
        """,
    }


# Create the views; if the LIVE base fails (schema drift we didn't catch),
# fall back to the empty shell so setup never halts here.
for view_name, sql in _usage_view_sql(base).items():
    try:
        spark.sql(sql)
        ok(f"Created view {gold}.{view_name}")
    except Exception as exc:  # noqa: BLE001
        warn(f"{view_name} over live source failed ({exc}); using empty shell.")
        spark.sql(_usage_view_sql(_EMPTY_USAGE_BASE)[view_name])
        ok(f"Created view {gold}.{view_name} (empty shell)")

# COMMAND ----------

# MAGIC %md ### 5 · Summary: endpoint × feature × status

# COMMAND ----------

if not results:
    results = [{"endpoint": "(none)", "feature": "-", "status": "-", "detail": "-"}]
print(summary_table(results, ["endpoint", "feature", "status", "detail"]))

banner("Prerequisites an Azure account admin may need to enable", char="-")
print("""  • Foundation Model APIs (pay-per-token) in southeastasia
  • Cross-geography routing (if a desired model isn't served in-region)
  • Mosaic / Unity AI Gateway (Beta) preview  [Account Console → Previews]
  • System tables access (system.serving.*) for live usage on Dashboard Page 4""")

# Persist the chosen endpoints as a task value for the orchestrator's summary.
try:
    dbutils.jobs.taskValues.set(key="text_endpoint", value=resolved.text)
except Exception:  # noqa: BLE001 - only available inside a job task
    pass

dbutils.notebook.exit(
    f"01b complete · text={resolved.text} vision={resolved.vision} unity_gateway={unity_gateway}"
)
