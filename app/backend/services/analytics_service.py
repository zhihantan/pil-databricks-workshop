"""Analytics service: KPI summary for the app home and AI usage widget.

Reads gold materialized/usage views via the SQL connector; falls back to
plausible demo values so the Home page always renders.
"""

from __future__ import annotations

from typing import Any

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.models.schemas import (
    KpiSummary,
    UsageDailyPoint,
    UsageEndpointPoint,
    UsageSummary,
)
from backend.services import demo_store

LOG = get_logger("backend.analytics_service")


class AnalyticsService:
    def __init__(self, sql_fn: Any = None, invoice_service: Any = None,
                 inspection_service: Any = None) -> None:
        self._sql_fn = sql_fn
        self._invoices = invoice_service
        self._inspections = inspection_service

    def kpi_summary(self) -> KpiSummary:
        settings = get_settings()
        pending = 0
        if self._invoices:
            pending = sum(1 for i in self._invoices.list_queue() if i.status == "pending")
        # Open work orders: Lakebase count when connected, else the demo store.
        open_wo = self._open_work_orders(settings)
        reliability = None
        utilization = None
        accuracy = None
        containers = 0
        invoices_done = 0
        if self._sql_fn:
            reliability = _scalar(self._sql_fn,
                f"SELECT ROUND(AVG(schedule_reliability_pct),1) v "
                f"FROM {settings.gold}.mv_daily_operations_kpis")
            utilization = _scalar(self._sql_fn,
                f"SELECT ROUND(AVG(vessel_utilization_pct),1) v "
                f"FROM {settings.gold}.mv_daily_operations_kpis")
            accuracy = _scalar(self._sql_fn,
                f"SELECT ROUND(100.0*AVG(is_correct),1) v "
                f"FROM {settings.silver}.container_inspections_scored")
            containers = int(_scalar(self._sql_fn,
                f"SELECT COUNT(*) v FROM {settings.silver}.container_inspections_scored") or 0)
            invoices_done = int(_scalar(self._sql_fn,
                f"SELECT COUNT(*) v FROM {settings.silver}.invoice_extractions") or 0)
        return KpiSummary(
            pending_reviews=pending,
            open_work_orders=open_wo,
            invoices_processed=invoices_done,
            containers_inspected=containers,
            inspection_accuracy_pct=accuracy,
            schedule_reliability_pct=reliability if reliability is not None else 73.0,
            vessel_utilization_pct=utilization if utilization is not None else 82.0,
        )

    def _open_work_orders(self, settings: Any) -> int:
        """Open work-order count: Lakebase when connected, else the demo store."""
        conn_factory = getattr(self._inspections, "_conn_factory", None)
        conn = conn_factory() if conn_factory else None
        if conn is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT COUNT(*) FROM pil_app.inspection_work_orders "
                        "WHERE status = 'open'")
                    return int(cur.fetchone()[0])
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Open work-order count failed: %s", exc)
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        return demo_store.open_work_order_count()

    def usage_summary(self) -> UsageSummary:
        settings = get_settings()
        series: list[UsageDailyPoint] = []
        all_rows: list[dict[str, Any]] = []
        by_endpoint: list[UsageEndpointPoint] = []
        if self._sql_fn:
            # Full daily history (all-time). We take the whole series for the
            # totals and keep the most recent 30 points for the trend chart.
            try:
                all_rows = list(self._sql_fn(
                    f"SELECT CAST(usage_date AS STRING) usage_date, "
                    f"COALESCE(total_tokens,0) total_tokens, "
                    f"COALESCE(request_count,0) request_count, "
                    f"COALESCE(est_cost_usd,0) est_cost_usd "
                    f"FROM {settings.gold}.v_ai_usage_daily ORDER BY usage_date"))
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Usage daily read failed: %s", exc)
            # Per-endpoint all-time breakdown (text vs vision), largest first.
            try:
                ep_rows = list(self._sql_fn(
                    f"SELECT endpoint, "
                    f"COALESCE(total_tokens,0) total_tokens, "
                    f"COALESCE(request_count,0) request_count, "
                    f"COALESCE(est_cost_usd,0) est_cost_usd "
                    f"FROM {settings.gold}.v_ai_usage_by_endpoint "
                    f"WHERE endpoint IS NOT NULL ORDER BY total_tokens DESC"))
                by_endpoint = [UsageEndpointPoint(**r) for r in ep_rows]
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Usage by-endpoint read failed: %s", exc)

        daily = [UsageDailyPoint(**r) for r in all_rows]
        series = daily[-30:]  # trend chart shows the most recent 30 days
        today = daily[-1] if daily else UsageDailyPoint(usage_date="today")
        return UsageSummary(
            today_tokens=today.total_tokens,
            today_requests=today.request_count,
            today_cost_usd=today.est_cost_usd,
            all_time_tokens=sum(d.total_tokens for d in daily),
            all_time_requests=sum(d.request_count for d in daily),
            all_time_cost_usd=round(sum(d.est_cost_usd for d in daily), 2),
            by_endpoint=by_endpoint,
            series=series,
        )


def _scalar(sql_fn: Any, sql: str) -> float | None:
    try:
        rows = list(sql_fn(sql))
        if rows:
            return next(iter(rows[0].values()))
    except Exception as exc:  # noqa: BLE001
        LOG.warning("Scalar query failed: %s", exc)
    return None
