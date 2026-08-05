"""Preflight checks for the orchestrator: verify privileges and capabilities
with clear pass/fail output before the workshop runs.

Each check returns a :class:`CheckResult`. Checks that require the Databricks
runtime accept ``spark``/``client`` and degrade to a SKIP (not a hard failure)
when a probe can't run, so a facilitator sees an actionable message rather than
a stack trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CheckResult:
    name: str
    status: str  # PASS | FAIL | WARN | SKIP
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status in ("PASS", "WARN", "SKIP")


def check_catalog_privilege(spark: Any, catalog: str) -> CheckResult:
    """Can we create (or already have) the target catalog?"""
    try:
        existing = [r[0] for r in spark.sql("SHOW CATALOGS").collect()]
        if catalog in existing:
            return CheckResult("Catalog access", "PASS",
                               f"'{catalog}' already exists.")
        # Try a dry-run create then leave it (01 will formally create it).
        spark.sql(f"CREATE CATALOG IF NOT EXISTS `{catalog}`")
        return CheckResult("Catalog create privilege", "PASS",
                           f"Created/verified '{catalog}'.")
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        m = msg.lower()
        # Azure "Default Storage" metastore with no storage root — a plain
        # CREATE CATALOG can't allocate managed storage. NOT a privilege issue.
        if ("storage root url does not exist" in m
                or "default storage is enabled" in m
                or "provide a storage location" in m):
            return CheckResult(
                "Catalog create (Default Storage)", "WARN",
                "Metastore has no storage root. Pre-create the catalog in the UI "
                "(Default Storage) OR set the 'managed_location' widget, then "
                f"re-run. Not a privilege issue. {msg[:70]}")
        return CheckResult(
            "Catalog create privilege", "FAIL",
            "Need CREATE CATALOG on the metastore (or a pre-created catalog with "
            f"ALL PRIVILEGES). {msg[:80]}")


def check_serverless_warehouse(client: Any) -> CheckResult:
    """Is a serverless SQL warehouse reachable?"""
    try:
        from .dbx_api import get_serverless_warehouse_id

        wid = get_serverless_warehouse_id(client)
        if wid:
            return CheckResult("Serverless SQL warehouse", "PASS", f"id={wid}")
        return CheckResult("Serverless SQL warehouse", "WARN",
                           "None found — dashboards/Genie need one. Create a "
                           "Serverless warehouse before the workshop.")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("Serverless SQL warehouse", "SKIP", str(exc)[:80])


def check_fmapi(client: Any, region: str) -> CheckResult:
    """Is at least one databricks-* FMAPI endpoint reachable in-region?"""
    try:
        names = {e.name for e in client.serving_endpoints.list() if e.name}
        fmapi = sorted(n for n in names if n.startswith("databricks-"))
        if fmapi:
            return CheckResult("FMAPI endpoints", "PASS",
                               f"{len(fmapi)} available (e.g. {fmapi[0]}).")
        return CheckResult(
            "FMAPI endpoints", "FAIL",
            f"No databricks-* endpoints in {region}. Enable Foundation Model "
            "APIs (pay-per-token) + cross-geography routing (account admin).")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("FMAPI endpoints", "SKIP", str(exc)[:80])


def check_unity_ai_gateway(client: Any, fmapi_name: str | None) -> CheckResult:
    """Is Unity AI Gateway governance reachable on a serving endpoint?"""
    if not fmapi_name:
        return CheckResult("Unity AI Gateway", "SKIP", "No FMAPI endpoint to probe.")
    try:
        ep = client.serving_endpoints.get(name=fmapi_name)
        if hasattr(ep, "ai_gateway"):
            return CheckResult("Unity AI Gateway", "PASS", "Governance reachable.")
        return CheckResult("Unity AI Gateway", "WARN",
                           "Not detected — endpoint-level config used; enable the "
                           "Mosaic/Unity AI Gateway preview (account admin).")
    except Exception as exc:  # noqa: BLE001
        return CheckResult("Unity AI Gateway", "SKIP", str(exc)[:80])


def check_genie(client: Any) -> CheckResult:
    """Is a Genie API surface present (partner-powered features may be gated)?"""
    genie = getattr(client, "genie", None)
    if genie is None:
        return CheckResult("Genie / partner-powered AI", "WARN",
                           "SDK Genie surface absent — create the space via UI. "
                           "Partner-powered features may need account+workspace "
                           "enablement in southeastasia.")
    return CheckResult("Genie / partner-powered AI", "PASS", "Genie surface present.")


def check_lakebase(client: Any) -> CheckResult:
    """Is the Lakebase (Database Instances) API available?"""
    if getattr(client, "database", None) is None:
        return CheckResult("Lakebase", "WARN",
                           "Database API absent — app runs UC-only until Lakebase "
                           "is enabled for the workspace.")
    return CheckResult("Lakebase", "PASS", "Database API present.")


def run_all(spark: Any, client: Any, catalog: str, region: str) -> list[CheckResult]:
    """Run every preflight check and return the results in display order."""
    results = [
        check_catalog_privilege(spark, catalog),
        check_serverless_warehouse(client),
    ]
    fmapi = check_fmapi(client, region)
    results.append(fmapi)
    # Extract a sample endpoint name from the FMAPI detail for the gateway probe.
    sample = None
    try:
        names = {e.name for e in client.serving_endpoints.list() if e.name}
        sample = next((n for n in sorted(names) if n.startswith("databricks-")), None)
    except Exception:  # noqa: BLE001
        pass
    results.append(check_unity_ai_gateway(client, sample))
    results.append(check_genie(client))
    results.append(check_lakebase(client))
    return results


def blocking_failures(results: list[CheckResult]) -> list[CheckResult]:
    """Return only hard FAILs (things that block a successful setup)."""
    return [r for r in results if r.status == "FAIL"]
