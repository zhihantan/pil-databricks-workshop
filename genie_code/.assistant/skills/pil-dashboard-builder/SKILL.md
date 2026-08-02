---
name: pil-dashboard-builder
description: >-
  Use when building or editing AI/BI dashboards over the pil_workshop catalog
  (PIL container-shipping data). Triggers on requests to create dashboard pages,
  KPI counters, trend charts, or tables from the gold tables and metric views
  (mv_* and metric_*). Encodes PIL's KPI definitions, chart-type guidance, layout
  conventions, the workshop color palette, and common pitfalls.
---

# PIL Dashboard Builder

You help workshop participants build AI/BI dashboards over **Pacific
International Lines (PIL)** container-shipping data in the `pil_workshop`
catalog. Follow the conventions below so every generated dashboard is correct,
consistent, and on-brand.

## When to use this skill

Activate when the user asks to build/extend an AI/BI dashboard, add a page, add
KPI counters/trends/tables, or "visualize" PIL operations, commercial,
sustainability, or AI-usage data. If the user references `@`-tables, prefer the
gold assets below.

## Data to use (and NOT use)

**Always build on the gold layer.** Never query `bronze` or `silver` directly —
they are raw/cleaning layers and will give wrong or unaggregated numbers.

Materialized views (use for time series and detail):
- `@mv_daily_operations_kpis` — daily `schedule_reliability_pct`,
  `vessel_utilization_pct`, `avg_arrival_delay_hrs`, `loaded_teu`.
- `@mv_port_performance` — per-port `avg_turnaround_hrs`, `avg_waiting_hrs`,
  `total_crane_moves`, `port_calls`.
- `@mv_customer_revenue` — per-customer `freight_revenue_usd`, `teu`, `bookings`.
- `@mv_container_utilization` — daily/route `utilization_pct`, `loaded_teu`,
  `capacity_teu`.

Metric views (use for ratio KPIs so definitions stay consistent — query with
`MEASURE(...)`):
- `@metric_schedule_reliability`, `@metric_delivery_transit`,
  `@metric_vessel_utilization`, `@metric_port_dwell_turnaround`,
  `@metric_revenue`, `@metric_sustainability`, `@metric_working_capital`.

Revenue/sustainability detail base views: `_rev_base`, `_sustainability_base`.

## KPI definitions (get these exactly right)

- **Schedule Reliability %** = on-time legs / total legs, where a leg is on-time
  when ATA ≤ ETA + 24h. Target band ~60–85%.
- **Vessel Utilization %** = loaded TEU / capacity TEU (manifest-based). ~70–95%.
- **On-Time Delivery %** — same 24h tolerance on delivery.
- **Port Dwell / Turnaround (hrs)** — dwell is container time in terminal;
  turnaround is vessel hours alongside.
- **Revenue per TEU** = (freight + demurrage & detention revenue) / TEU.
- **Fuel Efficiency** = mt fuel / 1,000 TEU-nm (lower is better).
- **CO₂ per TEU-km** — VLSFO factor ≈ 3.114 t CO₂ / t fuel.
- **DSO (days)** = avg issue→paid days over paid invoices.

## Chart-type guidance per KPI

| KPI / data | Chart |
|---|---|
| Single headline KPI (reliability, utilization, cost) | **Counter** |
| KPI over time (reliability, revenue per TEU, tokens) | **Line** |
| Ranking (worst ports, top customers, revenue by lane) | **Bar** (horizontal for long labels) |
| Two-measure relationship (waiting vs turnaround) | **Scatter** (size = volume) |
| Row-level detail (worst-10 ports, top customers) | **Table** |
| Category comparison (LNG vs VLSFO, CO₂ by class) | **Bar** |

## Layout conventions

1. **KPI counters across the top** (row height ~2), 2–3 per row.
2. **Trends in the middle** (line charts, full or half width, height ~3).
3. **Detail tables and rankings at the bottom** (height ~3).
4. One page per theme: *Fleet & Network Ops*, *Commercial*, *Sustainability*,
   *AI Usage & Governance*.
5. Add a short markdown text widget as a page header.

## Color palette (use consistently)

- Deep navy `#0B1F3A` (primary/headers), Ocean teal `#0E7C86` (positive series),
  Signal amber `#F5A623` (highlight), off-white `#F7F9FB` background.
- Categorical sequence: teal → amber → navy → seafoam `#5BB8B0` → coral `#E4572E`.
- Use coral only for negative/alert series (errors, overdue, delays).

## Filters (always add)

- A **global date-range** filter bound to the date field of each page's datasets.
- A **trade-lane** filter on commercial pages (from `_rev_base.trade_lane`).
- Enable **cross-filtering** so clicking a bar filters the rest of the page.

## Common pitfalls (avoid)

- ❌ Querying `bronze`/`silver` — always use gold `mv_*` / `metric_*`.
- ❌ Recomputing ratios by averaging percentages — use the metric views' `MEASURE()`.
- ❌ Counting individual containers for utilization — use `loaded_teu/capacity_teu`.
- ❌ Mixing currencies — revenue is pre-summed to USD in `_rev_base`.
- ❌ Forgetting the date filter, so counters show all-time instead of the period.

## Step-by-step instructions

1. Identify the theme → pick the page and its primary gold dataset.
2. Place KPI **counters** for the headline metrics at the top.
3. Add **line** charts for the key trends beneath them.
4. Add **bar**/**table** widgets for rankings/detail at the bottom.
5. Add the **global date filter** (+ trade-lane filter on commercial pages) and
   enable cross-filtering.
6. Apply the palette; label axes; give every widget a title.
7. Verify counters against the KPI bands above (e.g. reliability 60–85%).

## Few-shot examples

**Example 1 — Ops overview page**
> Build "Fleet Overview" from `@mv_daily_operations_kpis`: counters for
> `schedule_reliability_pct`, `vessel_utilization_pct`, `avg_arrival_delay_hrs`
> across the top; a full-width line of `schedule_reliability_pct` over
> `operations_date` below. Add a global date filter. Palette: teal series on
> off-white.

**Example 2 — Port performance**
> Add a "Ports" page: a scatter of `avg_waiting_hrs` (x) vs `avg_turnaround_hrs`
> (y) sized by `total_crane_moves` from `@mv_port_performance`, beside a table of
> the 10 worst ports by `avg_turnaround_hrs`. Cross-filter the scatter into the
> table.

**Example 3 — Sustainability**
> From `@metric_sustainability`, add a line of `Fuel Efficiency (mt per 1k
> TEU-nm)` by month colored by vessel class, and a bar comparing `fuel_eff`
> between LNG and VLSFO. Use amber for the highlight series; note lower is better.
