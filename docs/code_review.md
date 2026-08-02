# Code Review — PIL Workshop

Full correctness + compliance review of all assets, with resolutions. Reviews
were run across four surfaces: shared Python library, setup notebooks, the app
(backend + frontend), and master-prompt compliance. Style/lint were already
clean (ruff, ESLint, Prettier, tsc), so the review targeted **real bugs** and
**spec gaps**. All 15 notebooks pass a fresh-workspace dry-run; backend has 16
passing tests; library has 12.

## Findings fixed

| # | Severity | Area | Finding | Resolution |
|---|---|---|---|---|
| 1 | HIGH | `01b` | The three `CREATE VIEW` over `system.serving.endpoint_usage` were unguarded; a drifted system-table schema would crash step 2 and halt the whole orchestrator. | Probe now selects the specific columns (not `SELECT 1`); view creation is wrapped and falls back to an empty typed shell per-view, so setup never halts. |
| 2 | HIGH | app | Services are per-request (`Depends`), so the demo-mode in-memory queue didn't persist across requests; decisions could silently revert and work orders vanished. | Added `services/demo_store.py` — a process-level, locked store both services share. Decisions and work orders now persist across requests in demo mode. |
| 3 | MEDIUM | app | `open_work_orders` was hardcoded to 0, so creating a work order had no observable effect. | `analytics_service._open_work_orders` reads the Lakebase count when connected, else the demo store. |
| 4 | MEDIUM | app | `/api/health` reported `lakebase: true` whenever the SDK exposed a `.database` attribute (always), masking the fallback. | `lakebase_available()` now resolves the named instance (`get_database_instance`) to confirm real reachability. |
| 5 | MEDIUM | app | `list_inspections` built `InspectionItem` (strict `damage` enum) outside the try/except; an unexpected label would 500 the whole page. | Unknown `damage` labels are coerced to `None` before model construction. |
| 6 | MEDIUM | `99` | Teardown didn't delete the workspace-level model-serving endpoint from notebook 11 (it lives outside the catalog, so CASCADE misses it) → orphaned billable resource. | Added `serving_endpoints.delete("pil-spare-parts-forecaster")` to teardown. |
| 7 | LOW | frontend | Adjusting an invoice total to `0` sent `null` (`Number(x) || null`). | Parse explicitly; only send `null` when the field is blank. |
| 8 | LOW | `10` | Grant guidance printed the Lakebase instance as the catalog name. | Corrected to `pil-workshop-db`. |

## Spec-compliance gaps closed

| Item (master prompt §) | Was | Now |
|---|---|---|
| Booking Cancellation Rate as a **metric view** (§4.3) | Only in the KPI smoke test | `assets/metric_views/booking_cancellation.yml` → `gold.metric_booking_cancellation` |
| Port **container Dwell Time** as a metric view (§4.3) | Only vessel turnaround exposed | `assets/metric_views/container_dwell.yml` → `gold.metric_container_dwell` |
| Dashboard Page 4 tokens **by user** (§5.1/6.3) | View existed; no widget | Added `ds_ai_usage_user` dataset + `Usage by User` table on Page 4 |

## Reviewed and confirmed correct (no change needed)

- **restartPython variable scope** (notebooks 07/11/12): every post-restart cell
  correctly re-imports and re-derives all variables it uses. Not a bug.
- **Orchestrator step ordering** (00): valid producer→consumer dependency order;
  `99_teardown` is not in the step list.
- **All assets are scoped to the single `pil_workshop` catalog**: every notebook
  defaults `catalog` to `config.DEFAULT_CATALOG`; no DDL targets
  `main`/`default`/`hive_metastore`; no `USE CATALOG` redirects; every
  `saveAsTable`/DDL is catalog-qualified.
- **Zero ungoverned model calls**: external provider hosts appear only in
  `llm.py`'s `_FORBIDDEN_HOSTS` guard; no API keys; every model path routes
  through `pil_workshop.llm`.
- **Cross-layer contracts**: metric-view YAML columns ↔ silver/base-view columns,
  dashboard datasets ↔ gold objects, frontend TS types ↔ pydantic schemas,
  vision JSON schema ↔ `InspectionItem` — all aligned.

## Known limitations (documented, not defects)

- **base64 image fallback** (§7.1): not implemented; the Pillow generator is
  robust and always produces images, so the fallback was deemed unnecessary. Can
  be added if running in an environment without Pillow.
- **Invoice reconciliation source** (§7.2): reconciles extractions against
  `silver.invoice_pdf_ground_truth` (the disjoint set of generated PDFs) rather
  than `silver.invoices` — the functionally correct choice.
- **Empty Container Repositioning Cost**: delivered as the `gold.repositioning_plan`
  table (from OR-Tools) rather than a metric view — the natural home for an
  optimization output.

## Post-deployment fixes (live Job on fe-vm-zh-serverless)

After the first green Job runs, four assets were found not fully materialized on
the live workspace; all fixed and re-verified (the Job now recreates them from
scratch in one unaided run):

| Gap | Root cause | Fix |
|---|---|---|
| Genie space never created | Serverless runtime ships databricks-sdk 0.49 with **no `genie.create_space`** (added in 0.86); wrapper also passed wrong kwargs | Pin `databricks-sdk>=0.86` in the Job env; build the versioned `serialized_space` proto (`{"version":1,"data_sources":{"tables":[{"identifier":t}...sorted]}}`); handle `list_spaces().spaces` |
| ML model registered with 0 versions; no serving endpoint | one-step `registered_model_name=` didn't attach a version; runtime mlflow 2.21 needs `log_model(artifact_path=)`; `EndpointCoreConfigInput` needs `name=` | Two-step `log_model`→`register_model` capturing the version (try `name=`, fall back to `artifact_path=`); set an experiment; serve the captured version; add `name=` to the endpoint config |
| Dashboard Page 4 empty | `01b` usage views queried non-existent columns (`requesttime`, `served_entity_name`) | Use real schema `request_time` + join `system.serving.served_entities` for `endpoint_name`; 30-day window (now shows real gateway tokens/requests) |
| App served "not built" placeholder | `app/backend/static` was gitignored, so the Git-folder deploy had no SPA | Commit the built bundle (Apps deploy has no build step); redeploy |

Verified live: 13/13 Job tasks SUCCESS; Genie space, ML model + serving
endpoint, populated Page-4 usage views, and a RUNNING app with the real SPA.
