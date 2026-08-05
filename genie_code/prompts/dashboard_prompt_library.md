# PIL Dashboard — Genie Code Prompt Ladder

Paste these prompts, in order, into **Genie Code in Agent mode** from a **new
AI/BI dashboard canvas** in the `pil_workshop` catalog. Each level builds on the
previous one. `@table` references let Genie Code resolve the gold assets
directly. After each prompt, check the **Expected outcome** before moving on.

> **Prereqs:** Run setup notebooks 01–06 so the gold layer, materialized views,
> and metric views exist. Install the `pil-dashboard-builder` skill (see the
> participant guide) so Genie Code auto-applies PIL's KPI definitions, palette,
> and layout conventions — or `@`-mention it in your first prompt.

---

## L1 — Fleet Overview page

```
Create a dashboard page called "Fleet Overview" using
@mv_daily_operations_kpis. Add KPI counters for schedule reliability,
vessel utilization, and average arrival delay across the top, then a
full-width 12-month trend line of schedule_reliability_pct by
operations_date. Use the PIL palette (ocean teal series on off-white).
```

**Expected outcome ✅**
- A page "Fleet Overview" with 3 counters showing reliability (~60–85%),
  utilization (~70–95%), and avg delay (hrs).
- One line chart trending reliability over time.

---

## L2 — Port Performance page

```
Add a new page "Port Performance" from @mv_port_performance. Put a table of
the top/bottom 10 ports by average turnaround hours, and a dwell-time
heatmap or bar of avg_turnaround_hrs by region beside it. Sort the table
worst-first.
```

**Expected outcome ✅**
- A "Port Performance" page with a 10-row worst-ports table.
- A bar/heatmap of turnaround by region. Longest-turnaround ports stand out.

---

## L3 — Commercial page + global filters

```
Add a "Commercial" page using the revenue metric view @metric_revenue and
@mv_customer_revenue: a line of revenue per TEU by month, a horizontal bar
of total revenue by trade lane, and a table of the top 10 customers by
freight revenue. Then add a GLOBAL date-range filter and a trade-lane
filter that apply to every page, and enable cross-filtering.
```

**Expected outcome ✅**
- A "Commercial" page: revenue-per-TEU trend, revenue-by-lane bar, top-customers
  table.
- A global date filter + trade-lane filter in the dashboard header; clicking a
  trade-lane bar cross-filters the other widgets.

---

## L4 — Open-ended executive challenge

```
Design an executive summary page that answers a single question: "Are we
getting more fuel-efficient as vessel utilization rises?" Use
@metric_sustainability and @mv_container_utilization. Combine a dual-axis or
side-by-side view of fuel efficiency (mt per 1k TEU-nm, lower is better) and
vessel utilization % over the same months, and add a one-line insight text
widget stating the observed relationship. Keep the PIL palette; use coral
only if efficiency worsens.
```

**Expected outcome ✅**
- An "Executive Summary" page correlating fuel efficiency and utilization over
  time.
- A short written insight (e.g. "efficiency improves as utilization rises,
  suggesting better slot filling lowers fuel per TEU-nm").
- This is intentionally open — compare approaches across the room.

---

---

# New pages — build 3 fresh views the reference dashboard doesn't have

L1–L4 recreate the pre-built dashboard's themes (fleet ops, ports, commercial,
sustainability). The three prompts below build **new pages on data the reference
dashboard never shows** — receivables/finance, ML-driven inventory planning, and
the container-repositioning optimization plan. Each reads a **governed gold view**
(`v_financial_health`, `v_inventory_planning`, `v_repositioning_summary`) created
by setup (notebook 04, and 11/12 for the ML-backed ones), so the KPI logic lives
in the lakehouse, not the prompt.

## L5 — Financial Health & Receivables (CFO view)

```
Create a new dashboard page called "Financial Health" using
@v_financial_health. Across the top add KPI counters for average DSO
(dso_days), total cash at risk (sum of outstanding_usd), and dispute rate
(% of invoices where is_disputed = true). Below them add an accounts-
receivable AGING bar chart of sum(outstanding_usd) by aging_bucket, ordered
Current, 1-30 days, 31-60 days, 61-90 days, 90+ days. Beside it add a table
of the top 10 customers by outstanding_usd with their credit_terms. Use the
PIL palette and colour the 90+ days bucket in coral to flag risk.
```

**Expected outcome ✅**
- A "Financial Health" page with 3 counters (DSO in days, cash-at-risk in $, dispute %).
- An AR aging bar where the **90+ days** bucket dominates (worst-case cash tied up).
- A top-10 customers-by-outstanding table showing credit terms.

---

## L6 — Inventory & Demand Planning (surfaces the ML forecast)

```
Add a page "Inventory & Demand Planning" using @v_inventory_planning — this
view joins the LightGBM/Croston demand forecast to each spare-part's reorder
point. Add KPI counters for SKUs at risk of stockout (count where
stockout_risk = 'At risk') and total forecast inventory value (sum of
forecast_value_usd). Add a horizontal bar of at-risk SKU count by category,
a scatter of lead_time_demand vs reorder_point coloured by stockout_risk,
and a table of the top 15 SKUs by suggested_reorder_qty showing sku_code,
category, depot, lead_time_days and suggested_reorder_qty. PIL palette;
'At risk' points in coral.
```

**Expected outcome ✅**
- An "Inventory & Demand Planning" page with counters for at-risk SKUs (~200) and forecast value ($).
- A by-category at-risk bar and a lead-time-demand vs reorder-point scatter that separates 'At risk' (coral) from 'OK'.
- A reorder-recommendation table — the forecast from notebook 11 now driving a stocking decision.

---

## L7 — Empty-Container Repositioning (surfaces the optimization plan)

```
Add a page "Container Repositioning" using @v_repositioning_summary — the
OR-Tools min-cost-flow plan for moving empty containers. Add KPI counters
for total containers to move (sum of containers), total repositioning cost
(sum of cost_usd), and average cost per container. Add a bar of containers
by move_type (Intra-region vs Inter-region), a table of the top 10 lanes by
containers showing from_port, to_port, containers and cost_usd sorted
descending, and a bar of total cost_usd by from_region. Keep the PIL palette.
```

**Expected outcome ✅**
- A "Container Repositioning" page with counters for total containers, total cost, and cost/container.
- An intra- vs inter-region split (inter-region dominates cost) and a top-10 lanes table (e.g. Middle East → East Asia).
- Cost-by-origin-region bar — the optimization output from notebook 12 as an executive view.

---

## Tips for participants

- If a counter shows an implausible number, check you're on a **gold** asset
  (never `bronze`/`silver`) and that the date filter is applied.
- Ask Genie Code to "explain the SQL it generated" to learn the join paths.
- Use `MEASURE(\`Schedule Reliability %\`)` when reading a metric view directly.
- Re-prompt iteratively: "make the trend line teal", "add data labels",
  "sort descending" — Agent mode edits the canvas in place.
