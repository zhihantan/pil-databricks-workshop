"""Thin, patchable wrappers over Databricks REST/SDK calls whose request or
response shapes occasionally change between platform releases.

Per the workshop engineering standards, any API that *might* drift is isolated
here behind a small function with a docs-URL comment block, so it is trivial
to patch on-site during a workshop without touching notebook logic.

Everything degrades gracefully: each wrapper raises a ``DbxApiError`` with a
plain-language hint about the likely missing permission or preview toggle,
rather than a raw stack trace.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .utils import get_logger, retry

LOG = get_logger("pil_workshop.dbx_api")


class DbxApiError(RuntimeError):
    """Raised when a Databricks control-plane call fails, with a user hint."""

    def __init__(
        self, message: str, *, hint: str | None = None, cause: BaseException | None = None
    ) -> None:
        self.hint = hint
        full = message if not hint else f"{message}\n  ↳ Likely fix: {hint}"
        super().__init__(full)
        if cause is not None:
            self.__cause__ = cause


def _ws(client: Any | None) -> Any:
    """Return a ``WorkspaceClient`` (ambient auth inside notebooks/apps)."""
    if client is not None:
        return client
    try:
        from databricks.sdk import WorkspaceClient

        return WorkspaceClient()
    except Exception as exc:  # noqa: BLE001
        raise DbxApiError(
            "Could not initialize the Databricks SDK WorkspaceClient.",
            hint="Run inside a Databricks notebook/app, or set DATABRICKS_HOST/DATABRICKS_TOKEN.",
            cause=exc,
        ) from exc


# ===========================================================================
# Warehouses
# ===========================================================================
@retry(attempts=3)
def get_serverless_warehouse_id(client: Any | None = None) -> str | None:
    """Return the id of a running/available serverless SQL warehouse, if any.

    Docs: https://docs.databricks.com/api/workspace/warehouses/list
    """
    wc = _ws(client)
    try:
        best: str | None = None
        for wh in wc.warehouses.list():
            is_serverless = bool(getattr(wh, "enable_serverless_compute", False))
            if is_serverless:
                # Prefer a running one; otherwise remember the first serverless.
                state = str(getattr(wh, "state", "")).upper()
                if "RUNNING" in state:
                    return wh.id
                best = best or wh.id
        return best
    except Exception as exc:  # noqa: BLE001
        raise DbxApiError(
            "Failed to list SQL warehouses.",
            hint="Ensure you have access to a serverless SQL warehouse (CAN USE).",
            cause=exc,
        ) from exc


# ===========================================================================
# Lakeview (AI/BI) dashboards
# ===========================================================================
# Docs: https://docs.databricks.com/api/workspace/lakeview
# The Lakeview API surface (lakeview.create / publish) has evolved; keep the
# import + call shape here so it is a one-line patch if the SDK renames it.
@retry(attempts=2)
def create_or_update_lakeview_dashboard(
    display_name: str,
    serialized_dashboard: dict[str, Any] | str,
    warehouse_id: str,
    parent_path: str,
    client: Any | None = None,
) -> str:
    """Create (or update by name) a Lakeview dashboard; return its id.

    ``serialized_dashboard`` is the ``.lvdash.json`` content (dict or str).
    """
    wc = _ws(client)
    payload = (
        serialized_dashboard
        if isinstance(serialized_dashboard, str)
        else json.dumps(serialized_dashboard)
    )
    try:
        from databricks.sdk.service import dashboards as dbx_dash

        # Find an existing dashboard with the same name under parent_path.
        existing_id: str | None = None
        try:
            for d in wc.lakeview.list():
                if getattr(d, "display_name", None) == display_name:
                    existing_id = d.dashboard_id
                    break
        except Exception:  # noqa: BLE001 - listing may be paginated/limited
            pass

        dash = dbx_dash.Dashboard(
            display_name=display_name,
            serialized_dashboard=payload,
            warehouse_id=warehouse_id,
            parent_path=parent_path,
        )
        if existing_id:
            updated = wc.lakeview.update(dashboard_id=existing_id, dashboard=dash)
            dash_id = updated.dashboard_id
        else:
            created = wc.lakeview.create(dashboard=dash)
            dash_id = created.dashboard_id

        # Best-effort publish so it is immediately viewable.
        try:
            wc.lakeview.publish(dashboard_id=dash_id, warehouse_id=warehouse_id)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Dashboard created but publish step failed: %s", exc)
        return dash_id
    except DbxApiError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DbxApiError(
            f"Failed to create/update Lakeview dashboard '{display_name}'.",
            hint="Requires CAN MANAGE on the target folder and a valid "
            "serverless warehouse id. Verify the Lakeview API is available "
            "in this workspace.",
            cause=exc,
        ) from exc


def ensure_lakeview_schedule(
    dashboard_id: str,
    warehouse_id: str,
    *,
    cron: str = "0 30 * * * ?",
    timezone: str = "Asia/Singapore",
    display_name: str = "Hourly refresh",
    client: Any | None = None,
) -> str | None:
    """Attach (or update) a refresh schedule on a Lakeview dashboard.

    Idempotent: if a schedule with ``display_name`` already exists it is updated
    in place (matched by display name, then by etag), otherwise created. Aligns
    to the workshop's timezone; the default cron is hourly (at :30), between the
    top-of-hour setup-job rebuilds, so the dashboard re-queries fresh data every
    hour (and exercises the warehouse). Best-effort: returns the schedule id, or
    ``None`` if the Lakeview schedule API is unavailable / the call fails (the
    dashboard itself is unaffected). Requires a running warehouse to run.
    """
    wc = _ws(client)
    lakeview = getattr(wc, "lakeview", None)
    if lakeview is None or not hasattr(lakeview, "create_schedule"):
        LOG.info("Lakeview schedule API unavailable; skipping dashboard schedule.")
        return None
    try:
        from databricks.sdk.service import dashboards as dbx_dash

        cron_sched = dbx_dash.CronSchedule(
            quartz_cron_expression=cron, timezone_id=timezone
        )
        # Find an existing schedule with the same display name (idempotent re-run).
        existing = None
        try:
            for s in lakeview.list_schedules(dashboard_id=dashboard_id):
                if getattr(s, "display_name", None) == display_name:
                    existing = s
                    break
        except Exception:  # noqa: BLE001 - listing may be unsupported/empty
            pass

        if existing is not None:
            sched = dbx_dash.Schedule(
                cron_schedule=cron_sched, display_name=display_name,
                warehouse_id=warehouse_id, etag=getattr(existing, "etag", None),
            )
            updated = lakeview.update_schedule(
                dashboard_id=dashboard_id,
                schedule_id=existing.schedule_id, schedule=sched,
            )
            return getattr(updated, "schedule_id", existing.schedule_id)

        sched = dbx_dash.Schedule(
            cron_schedule=cron_sched, display_name=display_name,
            warehouse_id=warehouse_id,
        )
        created = lakeview.create_schedule(dashboard_id=dashboard_id, schedule=sched)
        return getattr(created, "schedule_id", None)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Could not attach dashboard schedule: %s", exc)
        ensure_lakeview_schedule.last_error = f"{type(exc).__name__}: {exc}"  # type: ignore[attr-defined]
        return None


# ===========================================================================
# Genie spaces
# ===========================================================================
# Docs: https://docs.databricks.com/api/workspace/genie/createspace
# genie.create_space takes a *serialized_space* string — a versioned "export
# proto". The v2 schema (discovered from an existing space via
# GET /api/2.0/genie/spaces/<id>?include_serialized_space=true) supports the
# full curation surface:
#   {"version": 2,
#    "data_sources": {"tables": [{"identifier": "<3-level>"}, ...]},   # SORTED
#    "instructions": {
#       "text_instructions":     [{"id": <hex>, "content": ["line\n", ...]}],
#       "example_question_sqls": [{"id": <hex>, "question": ["..."], "sql": ["..."],
#                                  "usage_guidance": ["..."]}]},
#    "benchmarks": {"questions": [{"id": <hex>, "question": ["..."],
#                                  "answer": [{"format": "SQL", "content": ["..."]}]}]}}
# So instructions, trusted example-SQL, and benchmarks all ship via the API.
def build_serialized_space(
    table_identifiers: list[str],
    text_instructions: str | None = None,
    example_sqls: list[dict[str, Any]] | None = None,
    benchmarks: list[dict[str, Any]] | None = None,
    sample_questions: list[str] | None = None,
) -> str:
    """Build a v2 ``serialized_space`` JSON string from workshop content.

    ``example_sqls`` items: {question, sql, usage_guidance?}.
    ``benchmarks`` items: {question, sql}.
    ``sample_questions``: the suggested questions shown in the Genie UI
    (stored under ``config.sample_questions``).
    Tables are sorted (the proto rejects unsorted). IDs are random 32-hex.
    """
    import json
    import uuid

    def _hid() -> str:
        return uuid.uuid4().hex

    def _lines(text: str) -> list[str]:
        # Preserve line structure the way the proto stores it.
        return [ln + "\n" for ln in text.rstrip("\n").split("\n")]

    space: dict[str, Any] = {
        "version": 2,
        "data_sources": {
            "tables": [{"identifier": t} for t in sorted(set(table_identifiers))]
        },
    }
    # The export proto requires id-bearing lists to be SORTED by id, so we sort
    # each list after assigning random hex ids.
    instructions: dict[str, Any] = {}
    if text_instructions and text_instructions.strip():
        instructions["text_instructions"] = sorted(
            [{"id": _hid(), "content": _lines(text_instructions)}],
            key=lambda x: x["id"],
        )
    if example_sqls:
        items = []
        for ex in example_sqls:
            item = {
                "id": _hid(),
                "question": [ex["question"]],
                "sql": [ex["sql"].strip()],
            }
            if ex.get("usage_guidance"):
                item["usage_guidance"] = [ex["usage_guidance"]]
            items.append(item)
        instructions["example_question_sqls"] = sorted(items, key=lambda x: x["id"])
    if instructions:
        space["instructions"] = instructions
    if benchmarks:
        questions = [
            {
                "id": _hid(),
                "question": [b["question"]],
                "answer": [{"format": "SQL", "content": [b["sql"].strip()]}],
            }
            for b in benchmarks
        ]
        space["benchmarks"] = {"questions": sorted(questions, key=lambda x: x["id"])}
    if sample_questions:
        # Suggested questions shown in the Genie UI live under config.sample_questions.
        sq = [{"id": _hid(), "question": [q]} for q in sample_questions]
        space["config"] = {"sample_questions": sorted(sq, key=lambda x: x["id"])}
    return json.dumps(space)


def create_genie_space(
    title: str,
    warehouse_id: str,
    table_identifiers: list[str],
    instructions: str,
    sample_questions: list[str],
    client: Any | None = None,
    description: str | None = None,
    parent_path: str | None = None,
    example_sqls: list[dict[str, Any]] | None = None,
    benchmarks: list[dict[str, Any]] | None = None,
) -> str | None:
    """Create a Genie space with instructions, example SQL, and benchmarks.

    Returns the space id, or ``None`` (rather than raising) when the API is
    unavailable so the caller can fall back to documented UI steps. The rich
    content is embedded in a v2 ``serialized_space`` via :func:`build_serialized_space`.
    """
    wc = _ws(client)
    genie = getattr(wc, "genie", None)
    create_fn = getattr(genie, "create_space", None) if genie else None
    if create_fn is None:
        LOG.info("SDK Genie space creation not available; caller should use UI.")
        create_genie_space.last_error = (  # type: ignore[attr-defined]
            "genie.create_space unavailable (needs databricks-sdk>=0.86)"
        )
        return None

    serialized = build_serialized_space(
        table_identifiers, text_instructions=instructions,
        example_sqls=example_sqls, benchmarks=benchmarks,
        sample_questions=sample_questions,
    )

    if parent_path is None:
        try:
            parent_path = f"/Users/{wc.current_user.me().user_name}"
        except Exception:  # noqa: BLE001
            parent_path = None

    try:
        kwargs: dict[str, Any] = {
            "warehouse_id": warehouse_id,
            "serialized_space": serialized,
            "title": title,
        }
        if description:
            kwargs["description"] = description
        if parent_path:
            kwargs["parent_path"] = parent_path
        space = create_fn(**kwargs)  # pragma: no cover - platform-only
        create_genie_space.last_error = None  # type: ignore[attr-defined]
        return getattr(space, "space_id", None) or getattr(space, "id", None)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Genie space API call failed (%s); fall back to UI.", exc)
        create_genie_space.last_error = f"{type(exc).__name__}: {exc}"  # type: ignore[attr-defined]
        return None


def update_genie_space(
    space_id: str,
    title: str,
    warehouse_id: str,
    table_identifiers: list[str],
    instructions: str,
    sample_questions: list[str],
    client: Any | None = None,
    description: str | None = None,
    example_sqls: list[dict[str, Any]] | None = None,
    benchmarks: list[dict[str, Any]] | None = None,
) -> bool:
    """Re-sync an existing Genie space with the current config (tables/instructions).

    So re-running setup after new gold views exist (e.g. the inventory/
    repositioning views built by notebooks 11/12) actually binds them, rather
    than leaving the reused space stale. Best-effort: returns True on success,
    False if the SDK lacks ``genie.update_space`` or the call fails (the caller
    keeps the existing space either way). Uses the same v2 ``serialized_space``.
    """
    wc = _ws(client)
    genie = getattr(wc, "genie", None)
    update_fn = getattr(genie, "update_space", None) if genie else None
    if update_fn is None:
        LOG.info("SDK genie.update_space unavailable; leaving existing space as-is.")
        update_genie_space.last_error = "genie.update_space unavailable"  # type: ignore[attr-defined]
        return False
    serialized = build_serialized_space(
        table_identifiers, text_instructions=instructions,
        example_sqls=example_sqls, benchmarks=benchmarks,
        sample_questions=sample_questions,
    )
    try:
        kwargs: dict[str, Any] = {
            "space_id": space_id,
            "warehouse_id": warehouse_id,
            "serialized_space": serialized,
            "title": title,
        }
        if description:
            kwargs["description"] = description
        update_fn(**kwargs)  # pragma: no cover - platform-only
        update_genie_space.last_error = None  # type: ignore[attr-defined]
        return True
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Genie space update failed (%s); existing space kept.", exc)
        update_genie_space.last_error = f"{type(exc).__name__}: {exc}"  # type: ignore[attr-defined]
        return False


# ===========================================================================
# Serving endpoints + AI Gateway config
# ===========================================================================
# Docs: https://docs.databricks.com/api/workspace/servingendpoints
#       https://docs.databricks.com/en/ai-gateway/configure-ai-gateway-endpoints.html
def get_serving_endpoint(name: str, client: Any | None = None) -> Any | None:
    """Return a serving endpoint object by name, or None if absent."""
    wc = _ws(client)
    try:
        return wc.serving_endpoints.get(name=name)
    except Exception as exc:  # noqa: BLE001
        LOG.info("Serving endpoint '%s' not found or unreadable: %s", name, exc)
        return None


@dataclass
class GatewayFeatureResult:
    """Outcome of trying to enable one AI Gateway feature on one endpoint."""

    endpoint: str
    feature: str
    status: str  # enabled | skipped | needs-admin | error
    detail: str = ""


def configure_ai_gateway(
    endpoint_name: str,
    *,
    usage_catalog: str,
    usage_schema: str,
    usage_table: str = "ai_gateway_usage",
    rate_limit_qpm: int | None = 50,
    enable_guardrails: bool = False,
    client: Any | None = None,
) -> list[GatewayFeatureResult]:
    """Best-effort per-feature configuration of endpoint-level AI Gateway.

    Each feature is attempted independently so a paid/preview-gated feature
    failing does not block the others. Returns a per-feature result list for
    the summary table in ``01b_ai_gateway_setup.py``.
    """
    wc = _ws(client)
    results: list[GatewayFeatureResult] = []

    try:
        from databricks.sdk.service.serving import (
            AiGatewayConfig,
            AiGatewayUsageTrackingConfig,
        )
    except Exception as exc:  # noqa: BLE001
        results.append(
            GatewayFeatureResult(
                endpoint_name,
                "sdk-import",
                "error",
                f"AI Gateway config types unavailable in installed SDK: {exc}",
            )
        )
        return results

    # --- Usage tracking (inference tables) ---
    try:
        usage_cfg = AiGatewayUsageTrackingConfig(enabled=True)
        gw = AiGatewayConfig(usage_tracking_config=usage_cfg)
        _put_ai_gateway(wc, endpoint_name, gw)
        results.append(
            GatewayFeatureResult(
                endpoint_name,
                "usage_tracking",
                "enabled",
                f"Inference/usage logging → system tables; surfaced in "
                f"{usage_catalog}.{usage_schema}.",
            )
        )
    except Exception as exc:  # noqa: BLE001
        results.append(
            GatewayFeatureResult(
                endpoint_name,
                "usage_tracking",
                "needs-admin",
                f"Could not enable usage tracking: {exc}",
            )
        )

    # --- Rate limits ---
    if rate_limit_qpm:
        try:
            from databricks.sdk.service.serving import (
                AiGatewayRateLimit,
                AiGatewayRateLimitKey,
                AiGatewayRateLimitRenewalPeriod,
            )

            rl = AiGatewayRateLimit(
                calls=rate_limit_qpm,
                key=AiGatewayRateLimitKey.USER,
                renewal_period=AiGatewayRateLimitRenewalPeriod.MINUTE,
            )
            gw = AiGatewayConfig(rate_limits=[rl])
            _put_ai_gateway(wc, endpoint_name, gw)
            results.append(
                GatewayFeatureResult(
                    endpoint_name,
                    "rate_limits",
                    "enabled",
                    f"{rate_limit_qpm} calls/user/min.",
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                GatewayFeatureResult(
                    endpoint_name,
                    "rate_limits",
                    "skipped",
                    f"Rate limit not applied: {exc}",
                )
            )

    # --- Guardrails (optional; depends on in-region moderation model) ---
    if enable_guardrails:
        try:
            from databricks.sdk.service.serving import (
                AiGatewayGuardrailParameters,
                AiGatewayGuardrails,
            )

            guard = AiGatewayGuardrails(
                input=AiGatewayGuardrailParameters(safety=True),
                output=AiGatewayGuardrailParameters(safety=True),
            )
            gw = AiGatewayConfig(guardrails=guard)
            _put_ai_gateway(wc, endpoint_name, gw)
            results.append(
                GatewayFeatureResult(
                    endpoint_name,
                    "guardrails",
                    "enabled",
                    "Safety filtering on input/output.",
                )
            )
        except Exception as exc:  # noqa: BLE001
            results.append(
                GatewayFeatureResult(
                    endpoint_name,
                    "guardrails",
                    "needs-admin",
                    f"Guardrails depend on FMAPI moderation-model availability in-region: {exc}",
                )
            )

    return results


def _put_ai_gateway(wc: Any, endpoint_name: str, gw: Any) -> None:
    """Call the SDK method that updates an endpoint's AI Gateway config.

    Method name has varied (``put_ai_gateway`` / ``update_ai_gateway``); try
    the known names so this is robust across SDK versions.
    """
    for method in ("put_ai_gateway", "update_ai_gateway"):
        fn = getattr(wc.serving_endpoints, method, None)
        if fn is not None:
            fn(name=endpoint_name, **_gw_kwargs(gw))
            return
    raise DbxApiError(
        "No AI Gateway update method found on serving_endpoints.",
        hint="Update databricks-sdk, or configure the gateway in the UI "
        "(Serving → endpoint → AI Gateway).",
    )


def _gw_kwargs(gw: Any) -> dict[str, Any]:
    """Spread an AiGatewayConfig into the kwargs the SDK method expects."""
    kwargs: dict[str, Any] = {}
    for attr in (
        "usage_tracking_config",
        "rate_limits",
        "guardrails",
        "inference_table_config",
        "fallback_config",
    ):
        val = getattr(gw, attr, None)
        if val is not None:
            kwargs[attr] = val
    return kwargs


# ===========================================================================
# Lakebase (managed Postgres) — Database Instances API
# ===========================================================================
# Docs: https://docs.databricks.com/api/workspace/database
# Lakebase is newer; wrap creation + credential minting so it is patchable.
def ensure_database_instance(
    name: str,
    capacity: str = "CU_1",
    client: Any | None = None,
) -> Any | None:
    """Create a Lakebase database instance if absent; return the instance.

    Returns ``None`` if the Database API is unavailable in this workspace so
    the caller can print enablement guidance.
    """
    wc = _ws(client)
    db = getattr(wc, "database", None)
    if db is None:
        LOG.info("Lakebase Database API not present in this SDK/workspace.")
        return None
    try:
        try:
            return db.get_database_instance(name=name)
        except Exception:  # noqa: BLE001 - not found → create
            pass
        from databricks.sdk.service.database import DatabaseInstance

        inst = DatabaseInstance(name=name, capacity=capacity)
        return db.create_database_instance(database_instance=inst)
    except Exception as exc:  # noqa: BLE001
        raise DbxApiError(
            f"Failed to ensure Lakebase instance '{name}'.",
            hint="Lakebase must be enabled for the workspace and you need "
            "permission to create database instances.",
            cause=exc,
        ) from exc


def get_database_credential(instance_name: str, client: Any | None = None) -> Any | None:
    """Mint a short-lived Lakebase credential via the SDK (never hardcode).

    Docs: https://docs.databricks.com/api/workspace/database/generatedatabasecredential
    """
    wc = _ws(client)
    db = getattr(wc, "database", None)
    if db is None:
        return None
    try:
        gen = getattr(db, "generate_database_credential", None)
        if gen is None:
            return None
        return gen(instance_names=[instance_name])
    except Exception as exc:  # noqa: BLE001
        raise DbxApiError(
            f"Failed to mint Lakebase credential for '{instance_name}'.",
            hint="Ensure the caller (or app service principal) can access the Lakebase instance.",
            cause=exc,
        ) from exc


# ===========================================================================
# Databricks Apps
# ===========================================================================
# Docs: https://docs.databricks.com/api/workspace/apps
def deploy_app(
    app_name: str,
    source_code_path: str,
    client: Any | None = None,
) -> Any | None:
    """Create (if needed) and deploy a Databricks App from a workspace path.

    Returns the deployment object, or ``None`` if the Apps API is unavailable.
    """
    wc = _ws(client)
    apps = getattr(wc, "apps", None)
    if apps is None:
        LOG.info("Databricks Apps API not present in this SDK/workspace.")
        return None
    try:
        from databricks.sdk.service.apps import App, AppDeployment

        try:
            wc.apps.get(name=app_name)
        except Exception:  # noqa: BLE001 - not found → create
            wc.apps.create(app=App(name=app_name))
            wc.apps.wait_get_app_active(name=app_name)

        deployment = AppDeployment(source_code_path=source_code_path)
        return wc.apps.deploy(app_name=app_name, app_deployment=deployment)
    except Exception as exc:  # noqa: BLE001
        raise DbxApiError(
            f"Failed to deploy Databricks App '{app_name}'.",
            hint="Databricks Apps must be enabled; you need CAN MANAGE on the "
            "app and the source path must exist in the workspace.",
            cause=exc,
        ) from exc


def app_service_principal_id(app_name: str, client: Any | None = None) -> str | None:
    """Return the client_id of the service principal a Databricks App runs as.

    Used to scope AI-usage views to the app's own calls (its SP is the
    ``requester`` recorded in ``system.serving.endpoint_usage``). Returns
    ``None`` if the app doesn't exist yet (e.g. usage views are built before the
    app is deployed on a fresh install) or the field isn't populated — callers
    degrade gracefully.
    """
    wc = _ws(client)
    apps = getattr(wc, "apps", None)
    if apps is None:
        return None
    try:
        app = wc.apps.get(name=app_name)
    except Exception as exc:  # noqa: BLE001 - app not created yet
        LOG.info("App '%s' not found (SP unresolved yet): %s", app_name, exc)
        return None
    # SDK field name has varied across versions; try the known spellings.
    for attr in ("service_principal_client_id", "service_principal_id", "service_principal_name"):
        val = getattr(app, attr, None)
        if val:
            return str(val)
    return None


def grant_app_warehouse_and_endpoints(
    app_sp: str,
    warehouse_id: str | None,
    endpoint_names: list[str] | None = None,
    client: Any | None = None,
) -> list[str]:
    """Grant the app SP CAN_USE on a warehouse and CAN_QUERY on serving endpoints.

    These are permission-API grants the SQL ``GRANT`` statement can't express
    (catalog/volume grants are done via SQL in the notebook). Best-effort:
    returns a list of human-readable outcome strings; never raises so setup
    doesn't halt if a grant already exists or the API shape differs.
    """
    wc = _ws(client)
    out: list[str] = []
    # SQL warehouse: CAN_USE
    if warehouse_id:
        try:
            from databricks.sdk.service.sql import (
                WarehouseAccessControlRequest,
                WarehousePermissionLevel,
            )

            wc.warehouses.update_permissions(
                warehouse_id=warehouse_id,
                access_control_list=[
                    WarehouseAccessControlRequest(
                        service_principal_name=app_sp,
                        permission_level=WarehousePermissionLevel.CAN_USE,
                    )
                ],
            )
            out.append(f"warehouse {warehouse_id}: CAN_USE")
        except Exception as exc:  # noqa: BLE001
            out.append(f"warehouse grant skipped: {str(exc)[:80]}")
    # Serving endpoints: CAN_QUERY. Built-in pay-per-token FMAPI endpoints
    # (``databricks-*``) are queryable by all workspace principals by default and
    # expose no permissionable id, so skip them — only custom endpoints need it.
    for name in endpoint_names or []:
        if name.startswith("databricks-"):
            out.append(f"endpoint {name}: built-in (open to all; no grant needed)")
            continue
        try:
            from databricks.sdk.service.serving import (
                ServingEndpointAccessControlRequest,
                ServingEndpointPermissionLevel,
            )

            ep = wc.serving_endpoints.get(name=name)
            eid = getattr(ep, "id", None)
            if not eid:
                out.append(f"endpoint {name}: no permissionable id, skipped")
                continue
            wc.serving_endpoints.update_permissions(
                serving_endpoint_id=eid,
                access_control_list=[
                    ServingEndpointAccessControlRequest(
                        service_principal_name=app_sp,
                        permission_level=ServingEndpointPermissionLevel.CAN_QUERY,
                    )
                ],
            )
            out.append(f"endpoint {name}: CAN_QUERY")
        except Exception as exc:  # noqa: BLE001
            out.append(f"endpoint {name} grant skipped: {str(exc)[:80]}")
    return out


# ===========================================================================
# Model serving for MLflow models (Phase 5)
# ===========================================================================
def ensure_model_serving_endpoint(
    endpoint_name: str,
    model_name: str,
    model_version: str,
    workload_size: str = "Medium",
    scale_to_zero: bool = False,
    client: Any | None = None,
) -> Any | None:
    """Create/update a model-serving endpoint for a UC-registered model.

    Defaults are sized for higher consumption: a **Medium** workload that is
    **always-on** (scale_to_zero=False) bills continuously rather than idling to
    zero. For a low-cost footprint use workload_size="Small", scale_to_zero=True.

    Docs: https://docs.databricks.com/api/workspace/servingendpoints/create
    """
    wc = _ws(client)
    try:
        from databricks.sdk.service.serving import (
            EndpointCoreConfigInput,
            ServedEntityInput,
        )

        served = ServedEntityInput(
            entity_name=model_name,
            entity_version=model_version,
            workload_size=workload_size,
            scale_to_zero_enabled=scale_to_zero,
        )
        # Some SDK versions require `name` on EndpointCoreConfigInput.
        cfg = EndpointCoreConfigInput(name=endpoint_name, served_entities=[served])
        try:
            wc.serving_endpoints.get(name=endpoint_name)
            return wc.serving_endpoints.update_config(name=endpoint_name, served_entities=[served])
        except Exception:  # noqa: BLE001 - not found → create
            return wc.serving_endpoints.create(name=endpoint_name, config=cfg)
    except Exception as exc:  # noqa: BLE001
        raise DbxApiError(
            f"Failed to ensure model serving endpoint '{endpoint_name}'.",
            hint="Requires serverless model serving enabled and CAN MANAGE on the UC model.",
            cause=exc,
        ) from exc
