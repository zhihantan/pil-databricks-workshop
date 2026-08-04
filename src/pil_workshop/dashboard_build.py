"""Programmatic builder for the AI/BI (Lakeview) dashboard.

Hand-writing a multi-page ``.lvdash.json`` by hand is error-prone, so we build
the dashboard dict here and serialize it. Running this module as a script
regenerates ``assets/dashboards/pil_operations.lvdash.json`` (guaranteed valid
JSON). Notebook ``05_create_dashboard.py`` imports the same builder so the
deployed dashboard and the committed file never drift.

The serialized-dashboard schema (datasets + pages + widgets) can evolve; the
deploy call is isolated in ``pil_workshop.dbx_api.create_or_update_lakeview_dashboard``
so it is a one-line patch if the API changes.

Dataset SQL uses the ``${catalog}`` token; :func:`build_dashboard` substitutes
the real catalog name. The committed file keeps the token so it stays portable.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .config import PALETTE, PALETTE_SEQUENCE

# ---------------------------------------------------------------------------
# Dataset definitions — the named queries the widgets read from.
# Kept catalog-agnostic via the ${catalog} token.
# ---------------------------------------------------------------------------
DATASETS: list[dict[str, Any]] = [
    {
        "name": "ds_daily_ops",
        "displayName": "Daily Operations KPIs",
        "query": "SELECT * FROM ${catalog}.gold.mv_daily_operations_kpis "
        "ORDER BY operations_date",
    },
    {
        "name": "ds_port_perf",
        "displayName": "Port Performance",
        "query": "SELECT * FROM ${catalog}.gold.mv_port_performance "
        "ORDER BY avg_turnaround_hrs DESC",
    },
    {
        "name": "ds_customer_rev",
        "displayName": "Customer Revenue",
        "query": "SELECT * FROM ${catalog}.gold.mv_customer_revenue "
        "ORDER BY freight_revenue_usd DESC",
    },
    {
        "name": "ds_container_util",
        "displayName": "Container Utilization",
        "query": "SELECT * FROM ${catalog}.gold.mv_container_utilization "
        "ORDER BY operations_date",
    },
    {
        "name": "ds_revenue_lane",
        "displayName": "Revenue by Trade Lane",
        "query": (
            "SELECT trade_lane, "
            "ROUND(SUM(freight_revenue_usd + dd_revenue_usd), 0) AS revenue_usd, "
            "SUM(teu) AS teu, "
            "ROUND(SUM(freight_revenue_usd + dd_revenue_usd)/NULLIF(SUM(teu),0),0) "
            "AS revenue_per_teu "
            "FROM ${catalog}.gold._rev_base GROUP BY trade_lane ORDER BY revenue_usd DESC"
        ),
    },
    {
        "name": "ds_revenue_month",
        "displayName": "Revenue per TEU Trend",
        "query": (
            "SELECT DATE_TRUNC('MONTH', revenue_date) AS month, "
            "ROUND(SUM(freight_revenue_usd + dd_revenue_usd)/NULLIF(SUM(teu),0),0) "
            "AS revenue_per_teu, "
            "ROUND(SUM(dd_revenue_usd),0) AS dd_revenue "
            "FROM ${catalog}.gold._rev_base GROUP BY 1 ORDER BY 1"
        ),
    },
    {
        "name": "ds_sustainability",
        "displayName": "Sustainability",
        "query": (
            "SELECT DATE_TRUNC('MONTH', leg_date) AS month, vessel_class, fuel_type, "
            "ROUND(SUM(fuel_consumed_mt)/NULLIF(SUM(teu_nm)/1000.0,0),4) AS fuel_eff, "
            "ROUND(SUM(co2_mt)*1000.0/NULLIF(SUM(teu_nm)*1.852,0),4) AS co2_per_teu_km "
            "FROM ${catalog}.gold._sustainability_base GROUP BY 1,2,3 ORDER BY 1"
        ),
    },
    {
        "name": "ds_ai_usage_daily",
        "displayName": "AI Usage Daily",
        "query": "SELECT * FROM ${catalog}.gold.v_ai_usage_daily ORDER BY usage_date",
    },
    {
        "name": "ds_ai_usage_endpoint",
        "displayName": "AI Usage by Endpoint",
        "query": "SELECT * FROM ${catalog}.gold.v_ai_usage_by_endpoint "
        "ORDER BY total_tokens DESC",
    },
    {
        "name": "ds_ai_usage_user",
        "displayName": "AI Usage by User",
        "query": "SELECT * FROM ${catalog}.gold.v_ai_usage_by_user "
        "ORDER BY total_tokens DESC",
    },
]


# ---------------------------------------------------------------------------
# Widget spec helpers (Lakeview widget spec versions).
# ---------------------------------------------------------------------------
def _counter(name: str, dataset: str, field: str, title: str) -> dict[str, Any]:
    return {
        "name": name,
        "queries": [
            {
                "name": f"q_{name}",
                "query": {
                    "datasetName": dataset,
                    "fields": [{"name": field, "expression": f"`{field}`"}],
                    "disaggregated": False,
                },
            }
        ],
        "spec": {
            # Lakeview counters require version 3; with version 2 the renderer
            # ignores the encodings and shows "Select fields to visualize".
            "version": 3,
            "widgetType": "counter",
            "encodings": {"value": {"fieldName": field, "displayName": title}},
            "frame": {"title": title, "showTitle": True},
        },
    }


def _line(
    name: str,
    dataset: str,
    x: str,
    y: str | list[str],
    title: str,
    color_field: str | None = None,
) -> dict[str, Any]:
    ys = [y] if isinstance(y, str) else y
    fields = [{"name": x, "expression": f"`{x}`"}]
    for yy in ys:
        fields.append({"name": yy, "expression": f"`{yy}`"})
    if color_field:
        fields.append({"name": color_field, "expression": f"`{color_field}`"})
    encodings: dict[str, Any] = {
        "x": {"fieldName": x, "scale": {"type": "temporal"}, "displayName": x},
        "y": {
            "fieldName": ys[0],
            "scale": {"type": "quantitative"},
            "displayName": ys[0],
        },
    }
    if color_field:
        encodings["color"] = {
            "fieldName": color_field,
            "scale": {"type": "categorical", "colors": PALETTE_SEQUENCE},
        }
    return {
        "name": name,
        "queries": [
            {
                "name": f"q_{name}",
                "query": {
                    "datasetName": dataset,
                    "fields": fields,
                    "disaggregated": True,
                },
            }
        ],
        "spec": {
            "version": 3,
            "widgetType": "line",
            "encodings": encodings,
            "frame": {"title": title, "showTitle": True},
        },
    }


def _bar(
    name: str, dataset: str, x: str, y: str, title: str, horizontal: bool = False
) -> dict[str, Any]:
    return {
        "name": name,
        "queries": [
            {
                "name": f"q_{name}",
                "query": {
                    "datasetName": dataset,
                    "fields": [
                        {"name": x, "expression": f"`{x}`"},
                        {"name": y, "expression": f"`{y}`"},
                    ],
                    "disaggregated": True,
                },
            }
        ],
        "spec": {
            "version": 3,
            "widgetType": "bar",
            "encodings": {
                "x": {"fieldName": x if not horizontal else y, "displayName": x},
                "y": {"fieldName": y if not horizontal else x, "displayName": y},
                "color": {"scale": {"colors": PALETTE_SEQUENCE}},
            },
            "frame": {"title": title, "showTitle": True},
        },
    }


def _scatter(
    name: str, dataset: str, x: str, y: str, size: str, title: str
) -> dict[str, Any]:
    return {
        "name": name,
        "queries": [
            {
                "name": f"q_{name}",
                "query": {
                    "datasetName": dataset,
                    "fields": [
                        {"name": x, "expression": f"`{x}`"},
                        {"name": y, "expression": f"`{y}`"},
                        {"name": size, "expression": f"`{size}`"},
                    ],
                    "disaggregated": True,
                },
            }
        ],
        "spec": {
            "version": 3,
            "widgetType": "scatter",
            "encodings": {
                "x": {"fieldName": x, "displayName": x},
                "y": {"fieldName": y, "displayName": y},
                "size": {"fieldName": size, "displayName": size},
            },
            "frame": {"title": title, "showTitle": True},
        },
    }


def _table(name: str, dataset: str, columns: list[str], title: str) -> dict[str, Any]:
    return {
        "name": name,
        "queries": [
            {
                "name": f"q_{name}",
                "query": {
                    "datasetName": dataset,
                    "fields": [{"name": c, "expression": f"`{c}`"} for c in columns],
                    "disaggregated": True,
                },
            }
        ],
        "spec": {
            "version": 1,
            "widgetType": "table",
            "encodings": {
                "columns": [{"fieldName": c, "displayName": c} for c in columns]
            },
            "frame": {"title": title, "showTitle": True},
        },
    }


def _text(name: str, markdown: str) -> dict[str, Any]:
    return {
        "name": name,
        "spec": {
            "version": 1,
            "widgetType": "text",
            "spec": {"text": markdown},
        },
        "textbox_spec": markdown,
    }


def _place(widget: dict[str, Any], x: int, y: int, w: int, h: int) -> dict[str, Any]:
    return {"widget": widget, "position": {"x": x, "y": y, "width": w, "height": h}}


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
def _page_ops() -> dict[str, Any]:
    layout = [
        _place(
            _text(
                "ops_hdr",
                f"## 🚢 Fleet & Network Operations\nDeep navy `{PALETTE['navy']}` · "
                f"ocean teal `{PALETTE['teal']}` · signal amber `{PALETTE['amber']}`",
            ),
            0,
            0,
            6,
            1,
        ),
        _place(
            _counter(
                "c_reliability",
                "ds_daily_ops",
                "schedule_reliability_pct",
                "Schedule Reliability %",
            ),
            0,
            1,
            2,
            2,
        ),
        _place(
            _counter(
                "c_util",
                "ds_daily_ops",
                "vessel_utilization_pct",
                "Vessel Utilization %",
            ),
            2,
            1,
            2,
            2,
        ),
        _place(
            _counter(
                "c_delay",
                "ds_daily_ops",
                "avg_arrival_delay_hrs",
                "Avg Arrival Delay (hrs)",
            ),
            4,
            1,
            2,
            2,
        ),
        _place(
            _line(
                "l_reliability",
                "ds_daily_ops",
                "operations_date",
                "schedule_reliability_pct",
                "Schedule Reliability Trend",
            ),
            0,
            3,
            6,
            3,
        ),
        _place(
            _scatter(
                "s_port",
                "ds_port_perf",
                "avg_waiting_hrs",
                "avg_turnaround_hrs",
                "total_crane_moves",
                "Port Performance (waiting vs turnaround)",
            ),
            0,
            6,
            3,
            3,
        ),
        _place(
            _table(
                "t_worst_ports",
                "ds_port_perf",
                ["port_name", "region", "avg_turnaround_hrs", "avg_waiting_hrs", "port_calls"],
                "Worst-10 Ports by Turnaround",
            ),
            3,
            6,
            3,
            3,
        ),
    ]
    return {"name": "page_ops", "displayName": "Fleet & Network Ops", "layout": layout}


def _page_commercial() -> dict[str, Any]:
    layout = [
        _place(_text("com_hdr", "## 💰 Commercial"), 0, 0, 6, 1),
        _place(
            _line(
                "l_rev_teu",
                "ds_revenue_month",
                "month",
                "revenue_per_teu",
                "Revenue per TEU Trend",
            ),
            0,
            1,
            3,
            3,
        ),
        _place(
            _line(
                "l_dd",
                "ds_revenue_month",
                "month",
                "dd_revenue",
                "Demurrage & Detention Revenue",
            ),
            3,
            1,
            3,
            3,
        ),
        _place(
            _bar(
                "b_lane",
                "ds_revenue_lane",
                "trade_lane",
                "revenue_usd",
                "Revenue by Trade Lane",
                horizontal=True,
            ),
            0,
            4,
            3,
            3,
        ),
        _place(
            _table(
                "t_customers",
                "ds_customer_rev",
                ["customer_name", "customer_type", "industry", "freight_revenue_usd", "teu"],
                "Top Customers by Revenue",
            ),
            3,
            4,
            3,
            3,
        ),
    ]
    return {
        "name": "page_commercial",
        "displayName": "Commercial",
        "layout": layout,
    }


def _page_sustainability() -> dict[str, Any]:
    layout = [
        _place(_text("sus_hdr", "## 🌱 Sustainability (IMO CII flavor)"), 0, 0, 6, 1),
        _place(
            _line(
                "l_fuel_eff",
                "ds_sustainability",
                "month",
                "fuel_eff",
                "Fuel Efficiency (mt / 1k TEU-nm)",
                color_field="vessel_class",
            ),
            0,
            1,
            6,
            3,
        ),
        _place(
            _bar(
                "b_co2_class",
                "ds_sustainability",
                "vessel_class",
                "co2_per_teu_km",
                "CO₂ per TEU-km by Vessel Class",
            ),
            0,
            4,
            3,
            3,
        ),
        _place(
            _bar(
                "b_fuel_type",
                "ds_sustainability",
                "fuel_type",
                "fuel_eff",
                "LNG vs VLSFO Fuel Efficiency",
            ),
            3,
            4,
            3,
            3,
        ),
    ]
    return {
        "name": "page_sustainability",
        "displayName": "Sustainability",
        "layout": layout,
    }


def _page_ai_governance() -> dict[str, Any]:
    layout = [
        _place(
            _text(
                "ai_hdr",
                "## 🤖 AI Usage & Governance\nLive Unity AI Gateway usage — "
                "populates as participants run Part 2.",
            ),
            0,
            0,
            6,
            1,
        ),
        _place(
            _counter("c_tokens", "ds_ai_usage_daily", "total_tokens", "Total Tokens"),
            0,
            1,
            2,
            2,
        ),
        _place(
            _counter(
                "c_requests", "ds_ai_usage_daily", "request_count", "Requests"
            ),
            2,
            1,
            2,
            2,
        ),
        _place(
            _counter("c_cost", "ds_ai_usage_daily", "est_cost_usd", "Est. Cost (USD)"),
            4,
            1,
            2,
            2,
        ),
        _place(
            _line(
                "l_tokens",
                "ds_ai_usage_daily",
                "usage_date",
                "total_tokens",
                "Tokens per Day",
            ),
            0,
            3,
            3,
            3,
        ),
        _place(
            _line(
                "l_errors",
                "ds_ai_usage_daily",
                "usage_date",
                "error_count",
                "Errors per Day",
            ),
            3,
            3,
            3,
            3,
        ),
        _place(
            _table(
                "t_endpoints",
                "ds_ai_usage_endpoint",
                ["endpoint", "request_count", "total_tokens", "error_count", "est_cost_usd"],
                "Usage by Endpoint",
            ),
            0,
            6,
            3,
            3,
        ),
        _place(
            _table(
                "t_users",
                "ds_ai_usage_user",
                ["user_name", "request_count", "total_tokens"],
                "Usage by User",
            ),
            3,
            6,
            3,
            3,
        ),
    ]
    return {
        "name": "page_ai_governance",
        "displayName": "AI Usage & Governance",
        "layout": layout,
    }


def build_dashboard(catalog: str = "${catalog}") -> dict[str, Any]:
    """Return the full serialized dashboard dict for ``catalog``."""
    datasets = []
    for ds in DATASETS:
        datasets.append(
            {
                "name": ds["name"],
                "displayName": ds["displayName"],
                "queryLines": [ds["query"].replace("${catalog}", catalog)],
            }
        )
    return {
        "datasets": datasets,
        "pages": [
            _page_ops(),
            _page_commercial(),
            _page_sustainability(),
            _page_ai_governance(),
        ],
        "uiSettings": {"theme": {"colors": PALETTE_SEQUENCE}},
    }


def _output_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.dirname(os.path.dirname(here))
    return os.path.join(repo, "assets", "dashboards", "pil_operations.lvdash.json")


def write_committed_file(path: str | None = None) -> str:
    """Write the catalog-tokenized dashboard JSON to the assets folder."""
    path = path or _output_path()
    dash = build_dashboard("${catalog}")
    with open(path, "w") as fh:
        json.dump(dash, fh, indent=2)
    return path


if __name__ == "__main__":  # regenerate the committed file
    out = write_committed_file()
    with open(out) as fh:
        n = len(json.load(fh)["pages"])
    print(f"Wrote {out} ({n} pages).")
