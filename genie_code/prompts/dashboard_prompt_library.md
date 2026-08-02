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

## Tips for participants

- If a counter shows an implausible number, check you're on a **gold** asset
  (never `bronze`/`silver`) and that the date filter is applied.
- Ask Genie Code to "explain the SQL it generated" to learn the join paths.
- Use `MEASURE(\`Schedule Reliability %\`)` when reading a metric view directly.
- Re-prompt iteratively: "make the trend line teal", "add data labels",
  "sort descending" — Agent mode edits the canvas in place.
