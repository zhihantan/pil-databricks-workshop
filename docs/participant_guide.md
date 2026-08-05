# Participant Guide — PIL Data + AI Workshop

Follow these numbered steps. Each has a ✅ checkpoint — if you don't see it, ask a
facilitator or check `docs/troubleshooting.md`.

---

## 0. Setup (once)

1. Open `setup/00_setup_all.py`.
2. Set widgets: `catalog=pil_workshop`, `scale=demo`.
3. **Run all**.

✅ You should see a preflight table, then per-step progress, then a summary with
asset URLs. Most steps show `OK`.

---

## Part 1 — Analytics & Genie

### 1. Explore the medallion data

1. Open **Catalog Explorer** → `pil_workshop`.
2. Look at `bronze.containers` (note dupes/nulls) vs `silver.containers` (clean,
   deduped). Open a Silver table's **columns** and read the comments.

✅ Every Silver column has a business comment; Bronze looks messy, Silver is clean.

### 2. The pre-built dashboard

1. Open the **PIL Operations** dashboard (URL in the setup summary).
2. Page through: Fleet & Network Ops, Commercial, Sustainability, AI Usage.

✅ KPI counters show plausible values (schedule reliability ~60–85%, utilization
~70–95%). Page 4 may be empty until Part 2 generates AI traffic.

### 3. Genie space Q&A

1. Open the **Genie** space (Genie → PIL Shipping Operations).
2. Ask a few sample questions, e.g. *"Which 5 ports have the worst average
   turnaround time?"* and *"What is revenue per TEU by trade lane?"*
3. Click **"Show generated code"** to see the SQL.

✅ Genie returns a chart/table and correct SQL using the gold layer.

### 4. Genie One

1. Open **Genie One** (business-user chat surface) over the same space.
2. Ask a question, pin the answer, and note you can invoke the app later.

✅ You can ask questions and pin results without writing SQL.

### 5. Genie Code — build your OWN dashboard

**Install the skill** (once):
1. Copy `genie_code/.assistant/skills/pil-dashboard-builder/` into your workspace
   at `Workspace/.assistant/skills/` (or your user-home `.assistant/skills/`).
2. Genie Code auto-loads skills from there; you can also `@`-mention
   `pil-dashboard-builder` in a prompt.

**Build with the prompt ladder** (`genie_code/prompts/dashboard_prompt_library.md`):
1. Open a **new AI/BI dashboard** canvas and switch **Genie Code to Agent mode**.
2. Paste **L1** (open-ended executive fuel-efficiency page). Check the ✅ expected outcome.
3. Continue **L2** (Financial Health & Receivables), **L3** (Inventory & Demand
   Planning — surfaces the ML forecast), **L4** (Empty-Container Repositioning —
   surfaces the optimization plan).

✅ After L4 you've built four new dashboard pages by prompting — including two that
put the Phase-5 ML forecast and optimization outputs in front of executives.

---

## Part 2 — Agentic Apps & ML

### 6. Invoice extraction (Agent Bricks)

1. Open `setup/08_agent_bricks_setup.py` output (or run it).
2. Read the Agent Bricks UI walkthrough; note the always-works `ai_query`
   fallback ran and populated `silver.invoice_extractions`.
3. Look at `gold.invoice_exceptions`.

✅ Exceptions include `total_mismatch`, `missing_po`, and `duplicate_no` rows —
the planted ~10% anomalies were caught.

### 7. Container vision

1. In notebook 08, find the container-vision eval.

✅ You see an accuracy % vs ground truth and a confusion breakdown by damage class.

### 8. The app

1. Open the **pil-invoice-vision** app (URL in the summary).
2. **Home:** KPI cards + the governed AI-usage widget.
3. **Invoice Review:** pick a queued invoice; the PDF is on the left, extracted
   fields on the right. Adjust a total or **Approve**/**Reject**.
4. **Container Inspections:** open a container, review the damage badge +
   confidence, click **Create work order**; try **Re-analyze**.

✅ Your decision is recorded and a toast confirms it. Re-open the dashboard's
**Page 4** — your app's model calls now appear there.

### 9. Lakebase loop

1. Note that approvals/rejections wrote to Lakebase (`pil_app.invoice_decisions`).
2. See `gold.invoice_decisions_synced` — the reverse-ETL target.

✅ You can trace: analytical exception → review queue → your decision → gold.

### 10. Forecasting lab

1. Open `setup/11_ml_forecasting.py` output.

✅ A model-comparison table (WAPE) with a champion; forecasts in
`gold.demand_forecasts`.

### 11. Route optimization lab

1. Open `setup/12_ml_route_optimization.py` output.

✅ A repositioning plan in `gold.repositioning_plan` with estimated savings, plus
a VRPTW drayage route list.

---

Congratulations — you've toured the full Databricks Data + AI platform end to end,
all governed through Unity Catalog and Unity AI Gateway. 🎉
