# Facilitator Guide — PIL Data + AI Workshop

A minute-by-minute run of show for a **full-day** workshop (Part 1 in the
morning, Part 2 in the afternoon). Half-day = Part 1 only.

---

## Before the day (prerequisites appendix — Azure-specific)

Arrange these **account-admin** toggles ahead of time; the preflight cell in
`00_setup_all` reports each one's status on the day:

- [ ] **Unity Catalog** metastore attached to the `southeastasia` workspace; you
      have `CREATE CATALOG` (or a pre-created `pil_workshop` catalog with ALL
      PRIVILEGES).
- [ ] **Serverless** compute + a **serverless SQL warehouse**.
- [ ] **Foundation Model APIs** (pay-per-token). If a desired model isn't served
      in `southeastasia`, enable **cross-geography routing** (Account Console →
      Settings → Feature enablement).
- [ ] **Mosaic / Unity AI Gateway (Beta)** preview (Account Console → Previews) —
      for central governance + usage tables feeding dashboard Page 4.
- [ ] **Partner-powered AI features** for Genie Code (account + workspace).
- [ ] **Databricks Apps** and **Lakebase** enabled for the workspace.

Run `setup/00_setup_all.py` with `scale=demo` **the night before** so tables,
dashboard, and Genie are warm. Build the app frontend (`npm install && npm run
build`) and pre-deploy the app if possible.

---

## Morning — Part 1 (Analytics & Genie) · ~3h

| Time | Segment | Notes / demo |
|---|---|---|
| 0:00–0:15 | **Welcome & context** | PIL's business: liner services, TEU, schedule reliability. Show the architecture diagram (`docs/architecture.md`). |
| 0:15–0:35 | **Medallion tour** | Open Catalog Explorer. Show Bronze messiness → Silver cleaning (constraints + column comments) → Gold. Emphasize comments drive Genie quality. |
| 0:35–1:05 | **The dashboard** | Walk the 4 pages. **Wow moment:** the KPI counters are live and land in industry bands (reliability 60–85%, utilization 70–95%). |
| 1:05–1:15 | ☕ Break | |
| 1:15–1:55 | **Genie space** | Ask the sample questions; show a **verified benchmark** answer and the generated SQL. Then show the same in **Genie One** (business-user surface). **Talk track:** business definitions live in the space instructions. |
| 1:55–2:45 | **Genie Code lab** | Participants build their own dashboard with the prompt ladder (`genie_code/prompts/`). Install the skill first (participant guide). Circulate; compare L4 executive pages. |
| 2:45–3:00 | **AI Gateway 5-min talk track** | Why the gateway matters for PIL: governed frontier-model tokens, budgets, **per-user rate limits**, auditability, guardrails. **Live demo:** trip the 50-QPM rate limit by firing repeated `ai_query` calls and show the throttle + the usage on Page 4. |

---

## Afternoon — Part 2 (Agentic Apps & ML) · ~3h

| Time | Segment | Notes / demo |
|---|---|---|
| 0:00–0:30 | **Agent Bricks: invoice extraction** | Show the UI Information Extraction agent over the invoice Volume, then the always-works `ai_query`/`ai_parse_document` fallback. Show `gold.invoice_exceptions` catching the ~10% planted anomalies. |
| 0:30–0:55 | **Container vision** | Run the vision `ai_query`; show the accuracy-vs-ground-truth eval and the confusion breakdown. |
| 0:55–1:05 | ☕ Break | |
| 1:05–1:40 | **The app** | Tour Home → Invoice Review (approve/adjust/reject) → Inspections (work order). **Wow moment:** decisions write to Lakebase and the app's model calls show up on dashboard **Page 4** alongside notebook traffic. |
| 1:40–2:00 | **Lakebase loop** | Explain reverse-ETL: exceptions → queue → human decision → `gold.invoice_decisions_synced`. |
| 2:00–2:40 | **Forecasting** | Compare Croston/TSB vs LightGBM on WAPE; champion → UC registry. Discuss why intermittency favors Croston for spares. |
| 2:40–3:00 | **Route optimization** | Min-cost-flow repositioning + savings vs naive; VRPTW drayage. Wrap-up + Q&A. |

---

## Wow moments (have these staged)

1. Live KPI counters that are *plausible* (not random) — proof the data is real.
2. Genie answering a hard "why" question with correct SQL.
3. Genie Code building a working dashboard page from one prompt.
4. Tripping a **rate limit** live, then watching tokens appear on **Page 4**.
5. An app invoice decision flowing back into the gold layer.

## Common failure recoveries

| Symptom | Fix |
|---|---|
| Preflight FAILs on FMAPI | Enable FMAPI + cross-geo routing; or set `PIL_TEXT_ENDPOINT` to a served model. |
| Dashboard empty | Assign a serverless warehouse to it; confirm notebooks 02–04 ran. |
| Genie API unavailable | Create the space via UI using `assets/genie/space_config.yml` (notebook 06 prints steps). |
| App page 404 on PDFs/images | Grant the app READ VOLUME; run notebook 07. |
| Lakebase absent | App runs UC-only (in-memory queue) — still fully clickable. |

See `docs/troubleshooting.md` for the full matrix.
