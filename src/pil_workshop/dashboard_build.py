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
    # Verbatim structure of a known-good Lakeview counter (dbdemos AIBI):
    # version 2, disaggregated=true, bare-column field, frame present.
    return {
        "name": name,
        "queries": [
            {
                "name": "main_query",
                "query": {
                    "datasetName": dataset,
                    "fields": [{"name": field, "expression": f"`{field}`"}],
                    "disaggregated": True,
                },
            }
        ],
        "spec": {
            "version": 2,
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
    # Verbatim structure of a known-good Lakeview line (dbdemos AIBI):
    # disaggregated=false, dimensions are raw columns, the measure is an
    # AGGREGATE field whose `name` is the agg label (e.g. "sum(y)") and whose
    # `expression` is the SQL agg. Encodings reference those exact names and
    # keep the `scale`. Our datasets are pre-aggregated MVs, so SUM() is a
    # harmless pass-through that gives Lakeview the aggregate form it requires.
    ys = [y] if isinstance(y, str) else y
    ymeas = f"sum({ys[0]})"
    fields = [{"name": x, "expression": f"`{x}`"}]
    if color_field:
        fields.append({"name": color_field, "expression": f"`{color_field}`"})
    fields.append({"name": ymeas, "expression": f"SUM(`{ys[0]}`)"})
    encodings: dict[str, Any] = {
        "x": {"fieldName": x, "scale": {"type": "temporal"}, "displayName": x},
        "y": {"fieldName": ymeas, "scale": {"type": "quantitative"}, "displayName": ys[0]},
    }
    if color_field:
        encodings["color"] = {
            "fieldName": color_field,
            "scale": {"type": "categorical"},
            "displayName": color_field,
        }
    return {
        "name": name,
        "queries": [
            {
                "name": "main_query",
                "query": {
                    "datasetName": dataset,
                    "fields": fields,
                    "disaggregated": False,
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
    # Known-good bar form: category dimension (raw) + aggregated measure,
    # disaggregated=false, both axes keep a scale (categorical / quantitative).
    ymeas = f"sum({y})"
    cat_enc = {"fieldName": x, "scale": {"type": "categorical"}, "displayName": x}
    meas_enc = {"fieldName": ymeas, "scale": {"type": "quantitative"}, "displayName": y}
    encodings = (
        {"x": cat_enc, "y": meas_enc}
        if not horizontal
        else {"x": meas_enc, "y": cat_enc}
    )
    return {
        "name": name,
        "queries": [
            {
                "name": "main_query",
                "query": {
                    "datasetName": dataset,
                    "fields": [
                        {"name": x, "expression": f"`{x}`"},
                        {"name": ymeas, "expression": f"SUM(`{y}`)"},
                    ],
                    "disaggregated": False,
                },
            }
        ],
        "spec": {
            "version": 3,
            "widgetType": "bar",
            "encodings": encodings,
            "frame": {"title": title, "showTitle": True},
        },
    }


def _scatter(
    name: str, dataset: str, x: str, y: str, size: str, title: str
) -> dict[str, Any]:
    # Verbatim structure of a known-good Lakeview scatter (Predictive
    # Maintenance dashboard): version 3, disaggregated=true (raw points), and
    # each x/y/size encoding carries a quantitative `scale`. Omitting the scale
    # (my earlier "minimal" attempt) made the renderer not pick the axes.
    return {
        "name": name,
        "queries": [
            {
                "name": "main_query",
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
                "x": {"fieldName": x, "scale": {"type": "quantitative"}, "displayName": x},
                "y": {"fieldName": y, "scale": {"type": "quantitative"}, "displayName": y},
                "size": {
                    "fieldName": size,
                    "scale": {"type": "quantitative"},
                    "displayName": size,
                },
            },
            "frame": {"title": title, "showTitle": True},
        },
    }


def _table_column(field: str, order: int, numeric: bool) -> dict[str, Any]:
    """Full Lakeview table-column encoding (matches a known-good table widget).

    A bare {fieldName, displayName} column does NOT render — Lakeview needs the
    complete column descriptor (type/displayAs/visible/order/title/…).
    """
    col: dict[str, Any] = {
        "fieldName": field,
        "booleanValues": ["false", "true"],
        "imageUrlTemplate": "{{ @ }}",
        "imageTitleTemplate": "{{ @ }}",
        "imageWidth": "",
        "imageHeight": "",
        "linkUrlTemplate": "{{ @ }}",
        "linkTextTemplate": "{{ @ }}",
        "linkTitleTemplate": "{{ @ }}",
        "linkOpenInNewTab": True,
        "type": "integer" if numeric else "string",
        "displayAs": "number" if numeric else "string",
        "visible": True,
        "order": 100000 + order,
        "title": field,
        "allowSearch": False,
        "alignContent": "right" if numeric else "left",
        "allowHTML": False,
        "highlightLinks": False,
        "useMonospaceFont": False,
        "preserveWhitespace": False,
        "displayName": field,
    }
    if numeric:
        col["numberFormat"] = "0.00"
    return col


def _table(
    name: str,
    dataset: str,
    columns: list[str],
    title: str,
    numeric: set[str] | None = None,
) -> dict[str, Any]:
    numeric = numeric or set()
    return {
        "name": name,
        "queries": [
            {
                "name": "main_query",
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
                "columns": [
                    _table_column(c, i, c in numeric) for i, c in enumerate(columns)
                ]
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
                numeric={"avg_turnaround_hrs", "avg_waiting_hrs", "port_calls"},
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
                numeric={"freight_revenue_usd", "teu"},
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
                numeric={"request_count", "total_tokens", "error_count", "est_cost_usd"},
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
                numeric={"request_count", "total_tokens"},
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


def _strip_text_and_compact(page: dict[str, Any]) -> dict[str, Any]:
    """Drop empty text/header widgets and compact the layout upward.

    The markdown text-header widgets render as blank strips (the tab name
    already serves as the heading), so they're removed. Any vertical gap they
    leave is closed by shifting every row up so the real visualizations reclaim
    the space and fill the page cleanly.
    """
    kept = [
        it
        for it in page["layout"]
        if it["widget"].get("spec", {}).get("widgetType") != "text"
    ]
    # Compact y: map the sorted distinct y-values to 0,1,2,... preserving each
    # widget's own height/relative order (row bands stay grouped).
    ys = sorted({it["position"]["y"] for it in kept})
    # Build new starting-y per original band by stacking heights of the tallest
    # widget in each band.
    new_y = 0
    y_to_new: dict[int, int] = {}
    for y in ys:
        y_to_new[y] = new_y
        band_h = max(it["position"]["height"] for it in kept if it["position"]["y"] == y)
        new_y += band_h
    for it in kept:
        it["position"]["y"] = y_to_new[it["position"]["y"]]
    return {**page, "layout": kept}


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
    pages = [
        _page_ops(),
        _page_commercial(),
        _page_sustainability(),
        _page_ai_governance(),
    ]
    pages = [_strip_text_and_compact(p) for p in pages]
    return {
        "datasets": datasets,
        "pages": pages,
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
