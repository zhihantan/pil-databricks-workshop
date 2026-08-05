# PIL Dashboard — Genie Code Prompt Ladder

Paste these prompts, in order, into **Genie Code in Agent mode** from a **new
AI/BI dashboard canvas** in the `pil_workshop` catalog. Each prompt adds a new
page to the dashboard. `@table` references let Genie Code resolve the gold assets
directly. After each prompt, check the **Expected outcome** before moving on.

> **Prereqs:** Run setup notebooks 01–06 so the gold layer, materialized views,
> and metric views exist — plus **11 and 12** for the inventory and repositioning
> pages (L3 and L4), which read the ML forecast and optimization outputs. Install
> the `pil-dashboard-builder` skill (see the participant guide) so Genie Code
> auto-applies PIL's KPI definitions, palette, and layout conventions — or
> `@`-mention it in your first prompt.

---

## L1 — Open-ended executive challenge

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

# New pages — build 3 fresh views the reference dashboard doesn't have

L1 above works from the pre-built dashboard's existing gold assets (the
sustainability / utilization theme). The three prompts below build **new pages on
data the reference dashboard never shows** — receivables/finance, ML-driven
inventory planning, and the container-repositioning optimization plan. Each reads
a **governed gold view** (`v_financial_health`, `v_inventory_planning`,
`v_repositioning_summary`) created by setup (notebook 04, and 11/12 for the
ML-backed ones), so the KPI logic lives in the lakehouse, not the prompt.

## L2 — Financial Health & Receivables (CFO view)

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

## L3 — Inventory & Demand Planning (surfaces the ML forecast)

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

## L4 — Empty-Container Repositioning (surfaces the optimization plan)

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
- When reading a metric view directly, aggregate with `MEASURE(...)` (e.g.
  `MEASURE(\`Fuel Efficiency\`)` on `@metric_sustainability`).
- Re-prompt iteratively: "make the trend line teal", "add data labels",
  "sort descending" — Agent mode edits the canvas in place.
