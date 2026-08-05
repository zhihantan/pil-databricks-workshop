"""Gold-layer construction: materialized views, metric-view base views, metric
views from YAML, and the KPI smoke tests.

Split out of the notebook for testability/patchability. The metric-view YAML in
``assets/metric_views/*.yml`` is the source of truth for measure/dimension
definitions; :func:`create_metric_views` reads those files, substitutes the
catalog, and issues ``CREATE OR REPLACE VIEW ... WITH METRICS LANGUAGE YAML``.
"""

from __future__ import annotations

import os
from typing import Any

from .config import KPI_RANGES
from .utils import get_logger

LOG = get_logger("pil_workshop.gold_build")

# CO2 emission factors (t CO2 / t fuel) by fuel type — IMO/IPCC style.
CO2_FACTOR = {"VLSFO": 3.114, "LNG": 2.750, "dual-fuel": 2.900}
DEFAULT_CO2_FACTOR = 3.114


def _g(catalog: str) -> str:
    return f"`{catalog}`.`gold`"


def _s(catalog: str) -> str:
    return f"`{catalog}`.`silver`"


# ---------------------------------------------------------------------------
# Base views that the revenue & sustainability metric views build on.
# ---------------------------------------------------------------------------
def create_base_views(spark: Any, catalog: str) -> None:
    """Create the helper base views used as metric-view sources."""
    g, s = _g(catalog), _s(catalog)

    # Revenue base: freight from bookings + D&D from invoice line items, keyed
    # to a revenue_date, trade lane, and customer attributes.
    spark.sql(f"""
    CREATE OR REPLACE VIEW {g}._rev_base AS
    WITH dd AS (
        SELECT i.booking_id,
               SUM(CASE WHEN li.charge_type IN ('Demurrage','Detention')
                        THEN li.amount * i.fx_to_usd ELSE 0 END) AS dd_usd
        FROM {s}.invoices i
        JOIN {s}.invoice_line_items li ON i.invoice_id = li.invoice_id
        GROUP BY i.booking_id
    )
    SELECT
        CAST(b.booking_ts AS DATE)                       AS revenue_date,
        CONCAT(po.region, ' → ', pd.region)              AS trade_lane,
        c.customer_type,
        c.industry,
        b.freight_rate_usd * b.container_count           AS freight_revenue_usd,
        COALESCE(dd.dd_usd, 0)                           AS dd_revenue_usd,
        b.container_count                                AS teu
    FROM {s}.bookings b
    JOIN {s}.customers c ON b.customer_id = c.customer_id
    JOIN {s}.ports po ON b.pol_port_id = po.port_id
    JOIN {s}.ports pd ON b.pod_port_id = pd.port_id
    LEFT JOIN dd ON b.booking_id = dd.booking_id
    WHERE b.is_cancelled = false
    """)

    # Sustainability base: leg-level fuel, TEU-nm, and CO2.
    factors = " ".join(f"WHEN '{k}' THEN {v}" for k, v in CO2_FACTOR.items())
    spark.sql(f"""
    CREATE OR REPLACE VIEW {g}._sustainability_base AS
    SELECT
        CAST(l.eta AS DATE)                     AS leg_date,
        v.vessel_class,
        v.fuel_type,
        l.fuel_consumed_mt,
        l.loaded_teu * l.distance_nm            AS teu_nm,
        l.fuel_consumed_mt * (CASE v.fuel_type {factors}
                              ELSE {DEFAULT_CO2_FACTOR} END) AS co2_mt
    FROM {s}.voyage_legs l
    JOIN {s}.voyages vo ON l.voyage_id = vo.voyage_id
    JOIN {s}.vessels v  ON vo.vessel_id = v.vessel_id
    WHERE l.eta IS NOT NULL
    """)
    LOG.info("Base views created: _rev_base, _sustainability_base")


# ---------------------------------------------------------------------------
# Materialized views. On serverless UC these are created with CREATE
# MATERIALIZED VIEW; a SCHEDULE clause documents refresh cadence. If MVs are
# unavailable we fall back to a regular view so the dashboard still works.
# ---------------------------------------------------------------------------
_MV_DEFS: dict[str, str] = {
    "mv_daily_operations_kpis": """
        SELECT
            CAST(l.eta AS DATE)                                   AS operations_date,
            COUNT(1)                                              AS leg_count,
            ROUND(100.0*SUM(CASE WHEN l.on_time THEN 1 ELSE 0 END)/NULLIF(COUNT(1),0),1)
                                                                  AS schedule_reliability_pct,
            ROUND(100.0*SUM(l.loaded_teu)/NULLIF(SUM(l.capacity_teu),0),1)
                                                                  AS vessel_utilization_pct,
            ROUND(AVG(l.arrival_delay_hrs),1)                     AS avg_arrival_delay_hrs,
            SUM(l.loaded_teu)                                     AS loaded_teu
        FROM {s}.voyage_legs l
        WHERE l.eta IS NOT NULL
        GROUP BY CAST(l.eta AS DATE)
    """,
    "mv_port_performance": """
        SELECT
            p.port_id, p.port_name, p.un_locode, p.region,
            COUNT(1)                                 AS port_calls,
            ROUND(AVG(pc.waiting_time_hrs),1)        AS avg_waiting_hrs,
            ROUND(AVG(pc.turnaround_hrs),1)          AS avg_turnaround_hrs,
            SUM(pc.crane_moves)                      AS total_crane_moves
        FROM {s}.port_calls pc
        JOIN {s}.ports p ON pc.port_id = p.port_id
        GROUP BY p.port_id, p.port_name, p.un_locode, p.region
    """,
    "mv_customer_revenue": """
        SELECT
            c.customer_id, c.customer_name, c.customer_type, c.industry,
            COUNT(DISTINCT b.booking_id)                          AS bookings,
            ROUND(SUM(b.freight_rate_usd*b.container_count),0)    AS freight_revenue_usd,
            SUM(b.container_count)                                AS teu
        FROM {s}.bookings b
        JOIN {s}.customers c ON b.customer_id = c.customer_id
        WHERE b.is_cancelled = false
        GROUP BY c.customer_id, c.customer_name, c.customer_type, c.industry
    """,
    "mv_container_utilization": """
        SELECT
            CAST(l.eta AS DATE)                      AS operations_date,
            vo.route_id,
            SUM(l.loaded_teu)                        AS loaded_teu,
            SUM(l.capacity_teu)                      AS capacity_teu,
            ROUND(100.0*SUM(l.loaded_teu)/NULLIF(SUM(l.capacity_teu),0),1)
                                                     AS utilization_pct
        FROM {s}.voyage_legs l
        JOIN {s}.voyages vo ON l.voyage_id = vo.voyage_id
        WHERE l.eta IS NOT NULL
        GROUP BY CAST(l.eta AS DATE), vo.route_id
    """,
}


def create_materialized_views(spark: Any, catalog: str) -> list[tuple[str, str]]:
    """Create the four MVs; return ``[(name, kind)]`` where kind is mv|view."""
    g, s = _g(catalog), _s(catalog)
    results: list[tuple[str, str]] = []
    for name, body in _MV_DEFS.items():
        sql_body = body.format(s=s)
        target = f"{g}.{name}"
        try:
            spark.sql(
                f"CREATE MATERIALIZED VIEW IF NOT EXISTS {target} "
                f"SCHEDULE EVERY 1 DAY AS {sql_body}"
            )
            results.append((name, "mv"))
            LOG.info("Materialized view %s created.", name)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("MV %s unavailable (%s); creating plain view.", name, exc)
            spark.sql(f"CREATE OR REPLACE VIEW {target} AS {sql_body}")
            results.append((name, "view"))
    return results


# ---------------------------------------------------------------------------
# Analytics views for the Genie Code "build-your-own dashboard" pages (L5-L7).
# Plain governed gold VIEWs (not scheduled MVs) — they read from already-built
# gold/silver, so the KPI logic (AR aging, forecast-vs-reorder, repositioning
# savings) lives in a governed layer instead of ad-hoc dashboard prompts.
# ---------------------------------------------------------------------------
_ANALYTICS_VIEW_DEFS: dict[str, str] = {
    # L5 — Financial Health & Receivables (CFO / AR view).
    "v_financial_health": """
        SELECT
            i.invoice_id, i.invoice_no, i.customer_id,
            c.customer_name, c.customer_type, c.industry, c.credit_terms,
            i.issue_date, i.due_date, i.paid_date, i.status, i.is_disputed,
            i.total_usd,
            -- days sales outstanding: paid → issue-to-paid, else issue-to-today
            DATEDIFF(COALESCE(i.paid_date, CURRENT_DATE), i.issue_date)  AS dso_days,
            -- overdue aging (open/overdue only), bucketed for an AR aging chart
            CASE WHEN i.status IN ('Paid') THEN 0
                 ELSE GREATEST(DATEDIFF(CURRENT_DATE, i.due_date), 0) END AS days_overdue,
            CASE
                WHEN i.status = 'Paid' THEN 'Paid'
                WHEN DATEDIFF(CURRENT_DATE, i.due_date) <= 0 THEN 'Current'
                WHEN DATEDIFF(CURRENT_DATE, i.due_date) <= 30 THEN '1-30 days'
                WHEN DATEDIFF(CURRENT_DATE, i.due_date) <= 60 THEN '31-60 days'
                WHEN DATEDIFF(CURRENT_DATE, i.due_date) <= 90 THEN '61-90 days'
                ELSE '90+ days'
            END                                                          AS aging_bucket,
            -- cash at risk = unpaid balance (open/overdue/disputed)
            CASE WHEN i.status <> 'Paid' THEN i.total_usd ELSE 0 END      AS outstanding_usd
        FROM {s}.invoices i
        JOIN {s}.customers c ON i.customer_id = c.customer_id
    """,
    # L6 — Inventory & Demand Planning (surfaces the ML forecast output).
    # Aggregate the per-day forecast to horizon-total demand per SKU, then join
    # the SKU master to compare against reorder point + value the exposure.
    "v_inventory_planning": """
        WITH fc AS (
            SELECT sku_id, segment,
                   ROUND(SUM(forecast_qty), 1)              AS forecast_horizon_qty,
                   ROUND(AVG(forecast_qty), 3)              AS forecast_daily_qty,
                   COUNT(*)                                 AS horizon_days
            FROM {g}.demand_forecasts
            GROUP BY sku_id, segment
        )
        SELECT
            sp.sku_id, sp.sku_code, sp.category, sp.depot,
            sp.demand_pattern, sp.unit_cost_usd, sp.lead_time_days, sp.reorder_point,
            fc.segment, fc.forecast_horizon_qty, fc.forecast_daily_qty, fc.horizon_days,
            -- projected demand over the reorder lead time
            ROUND(fc.forecast_daily_qty * sp.lead_time_days, 1)          AS lead_time_demand,
            -- stockout risk flag: lead-time demand exceeds the reorder point
            CASE WHEN fc.forecast_daily_qty * sp.lead_time_days > sp.reorder_point
                 THEN 'At risk' ELSE 'OK' END                            AS stockout_risk,
            -- recommended reorder qty (cover horizon demand above reorder point)
            GREATEST(ROUND(fc.forecast_horizon_qty - sp.reorder_point, 0), 0)
                                                                         AS suggested_reorder_qty,
            -- inventory value of the forecasted horizon demand
            ROUND(fc.forecast_horizon_qty * sp.unit_cost_usd, 2)         AS forecast_value_usd
        FROM {s}.spare_parts sp
        JOIN fc ON sp.sku_id = fc.sku_id
    """,
    # L7 — Empty-Container Repositioning (surfaces the optimization output).
    "v_repositioning_summary": """
        SELECT
            rp.from_port_id, rp.from_port, fp.region  AS from_region, fp.country AS from_country,
            rp.to_port_id, rp.to_port, tp.region      AS to_region,   tp.country AS to_country,
            rp.containers, rp.cost_usd,
            ROUND(rp.cost_usd / NULLIF(rp.containers, 0), 1)            AS cost_per_container,
            CASE WHEN fp.region = tp.region THEN 'Intra-region'
                 ELSE 'Inter-region' END                               AS move_type
        FROM {g}.repositioning_plan rp
        LEFT JOIN {s}.ports fp ON rp.from_port_id = fp.port_id
        LEFT JOIN {s}.ports tp ON rp.to_port_id = tp.port_id
    """,
}


def create_analytics_views(spark: Any, catalog: str) -> list[str]:
    """Create the L5-L7 analytics gold views; return created names.

    Best-effort per view: a missing upstream table (e.g. demand_forecasts before
    notebook 11 runs) skips that view with a warning rather than failing gold.
    """
    g, s = _g(catalog), _s(catalog)
    created: list[str] = []
    for name, body in _ANALYTICS_VIEW_DEFS.items():
        target = f"{g}.{name}"
        try:
            spark.sql(f"CREATE OR REPLACE VIEW {target} AS {body.format(g=g, s=s)}")
            created.append(name)
            LOG.info("Analytics view %s created.", name)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Analytics view %s skipped (upstream not ready?): %s", name, exc)
    return created


# ---------------------------------------------------------------------------
# Metric views from YAML
# ---------------------------------------------------------------------------
# Map YAML filename -> gold view name.
_METRIC_VIEW_FILES: dict[str, str] = {
    "schedule_reliability.yml": "metric_schedule_reliability",
    "delivery_transit.yml": "metric_delivery_transit",
    "vessel_utilization.yml": "metric_vessel_utilization",
    "port_dwell_turnaround.yml": "metric_port_dwell_turnaround",
    "revenue.yml": "metric_revenue",
    "sustainability.yml": "metric_sustainability",
    "working_capital.yml": "metric_working_capital",
    "booking_cancellation.yml": "metric_booking_cancellation",
    "container_dwell.yml": "metric_container_dwell",
}


def _metric_views_dir() -> str:
    """Locate ``assets/metric_views`` relative to the repo root."""
    here = os.path.dirname(os.path.abspath(__file__))  # src/pil_workshop
    repo = os.path.dirname(os.path.dirname(here))  # repo root
    return os.path.join(repo, "assets", "metric_views")


def create_metric_views(
    spark: Any, catalog: str, metric_dir: str | None = None
) -> list[tuple[str, str]]:
    """Create metric views from the YAML specs. Returns ``[(name, status)]``."""
    g = _g(catalog)
    mdir = metric_dir or _metric_views_dir()
    results: list[tuple[str, str]] = []
    for fname, view_name in _METRIC_VIEW_FILES.items():
        path = os.path.join(mdir, fname)
        target = f"{g}.{view_name}"
        try:
            with open(path) as fh:
                yaml_text = fh.read().replace("${catalog}", catalog)
            # The body is wrapped in a $$...$$ dollar-quote, so a literal '$$'
            # anywhere inside (e.g. in a comment) would close the quote early and
            # break parsing. Strip any '$$' — it is never valid in the YAML body.
            if "$$" in yaml_text:
                LOG.warning("Stripping stray '$$' from %s metric-view body.", fname)
                yaml_text = yaml_text.replace("$$", "")
            spark.sql(
                f"CREATE OR REPLACE VIEW {target} WITH METRICS LANGUAGE YAML AS $$\n{yaml_text}\n$$"
            )
            results.append((view_name, "created"))
            LOG.info("Metric view %s created from %s.", view_name, fname)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Metric view %s failed (%s).", view_name, exc)
            results.append((view_name, f"failed: {str(exc)[:60]}"))
    return results


# ---------------------------------------------------------------------------
# KPI smoke tests — the numbers a facilitator sanity-checks live.
# ---------------------------------------------------------------------------
def compute_kpi_summary(spark: Any, catalog: str) -> dict[str, float]:
    """Compute the headline KPIs directly from Silver for the summary table."""
    s = _s(catalog)

    def scalar(sql: str) -> float:
        v = spark.sql(sql).collect()[0][0]
        return float(v) if v is not None else 0.0

    kpis = {
        "schedule_reliability_pct": scalar(
            f"SELECT 100.0*SUM(CASE WHEN on_time THEN 1 ELSE 0 END)/NULLIF(COUNT(1),0) "
            f"FROM {s}.voyage_legs WHERE eta IS NOT NULL"
        ),
        "on_time_delivery_pct": scalar(
            f"SELECT 100.0*SUM(CASE WHEN on_time THEN 1 ELSE 0 END)/NULLIF(COUNT(1),0) "
            f"FROM {s}.voyage_legs WHERE ata IS NOT NULL"
        ),
        "vessel_utilization_pct": scalar(
            f"SELECT 100.0*SUM(loaded_teu)/NULLIF(SUM(capacity_teu),0) FROM {s}.voyage_legs"
        ),
        "avg_port_dwell_hrs": scalar(
            f"SELECT AVG(dwell_hrs) FROM {s}.shipments WHERE dwell_hrs > 0"
        ),
        "avg_turnaround_hrs": scalar(f"SELECT AVG(turnaround_hrs) FROM {s}.port_calls"),
        "revenue_per_teu_usd": scalar(
            f"SELECT SUM(freight_rate_usd*container_count)/NULLIF(SUM(container_count),0) "
            f"FROM {s}.bookings WHERE is_cancelled=false"
        ),
        "booking_cancellation_rate_pct": scalar(
            f"SELECT 100.0*SUM(CASE WHEN is_cancelled THEN 1 ELSE 0 END)/NULLIF(COUNT(1),0) "
            f"FROM {s}.bookings"
        ),
        "dso_days": scalar(
            f"SELECT AVG(DATEDIFF(paid_date, issue_date)) FROM {s}.invoices "
            f"WHERE paid_date IS NOT NULL"
        ),
        "fuel_efficiency_mt_per_1k_teu_nm": scalar(
            f"SELECT SUM(fuel_consumed_mt)/NULLIF(SUM(loaded_teu*distance_nm)/1000.0,0) "
            f"FROM {s}.voyage_legs"
        ),
    }
    return {k: round(v, 3) for k, v in kpis.items()}


def check_kpis(kpis: dict[str, float]) -> list[dict[str, Any]]:
    """Compare KPIs to :data:`KPI_RANGES`; return per-KPI pass/fail rows."""
    rows: list[dict[str, Any]] = []
    for name, val in kpis.items():
        lo, hi = KPI_RANGES.get(name, (float("-inf"), float("inf")))
        rows.append(
            {
                "kpi": name,
                "value": val,
                "expected": f"{lo}–{hi}",
                "status": "PASS" if lo <= val <= hi else "OUT-OF-RANGE",
            }
        )
    return rows
