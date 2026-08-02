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


# ===========================================================================
# Genie spaces
# ===========================================================================
# Docs: https://docs.databricks.com/api/workspace/genie/createspace
# The SDK's genie.create_space takes a *serialized_space* string — a versioned
# "export proto" (version 1/2). The minimal valid payload that binds a space to
# tables is:
#   {"version": 1, "data_sources": {"tables": [{"identifier": "<3-level>"}, ...]}}
# with the tables SORTED by identifier. Instructions/sample-questions are not
# expressible in this proto version, so they remain documented for the UI and
# live in assets/genie/space_config.yml. Discovered empirically on serverless.
def create_genie_space(
    title: str,
    warehouse_id: str,
    table_identifiers: list[str],
    instructions: str,
    sample_questions: list[str],
    client: Any | None = None,
    description: str | None = None,
    parent_path: str | None = None,
) -> str | None:
    """Create a Genie space bound to ``table_identifiers``; return space id or None.

    Returns ``None`` (rather than raising) when the API is unavailable, so the
    setup notebook can fall back to documented UI steps. ``instructions`` and
    ``sample_questions`` are accepted for signature compatibility but are added
    via the UI (not supported by the serialized-space proto here).
    """
    import json

    wc = _ws(client)
    genie = getattr(wc, "genie", None)
    create_fn = getattr(genie, "create_space", None) if genie else None
    if create_fn is None:
        LOG.info("SDK Genie space creation not available; caller should use UI.")
        return None

    # Tables MUST be sorted by identifier or the export proto is rejected.
    tables = [{"identifier": t} for t in sorted(set(table_identifiers))]
    serialized = json.dumps({"version": 1, "data_sources": {"tables": tables}})

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
        return getattr(space, "space_id", None) or getattr(space, "id", None)
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Genie space API call failed (%s); fall back to UI.", exc)
        create_genie_space.last_error = f"{type(exc).__name__}: {exc}"  # type: ignore[attr-defined]
        return None


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


# ===========================================================================
# Model serving for MLflow models (Phase 5)
# ===========================================================================
def ensure_model_serving_endpoint(
    endpoint_name: str,
    model_name: str,
    model_version: str,
    workload_size: str = "Small",
    scale_to_zero: bool = True,
    client: Any | None = None,
) -> Any | None:
    """Create/update a model-serving endpoint for a UC-registered model.

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
