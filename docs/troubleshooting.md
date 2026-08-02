# Troubleshooting

Grouped by phase. Most issues are missing account/workspace enablement in
`southeastasia` or a missing serverless warehouse.

---

## Setup / orchestrator (00, 01)

| Symptom | Cause | Fix |
|---|---|---|
| Preflight FAIL: *Catalog create privilege* | No `CREATE CATALOG` on the metastore. | Ask a metastore admin to grant it, or to pre-create `pil_workshop` and grant you ALL PRIVILEGES. |
| `00_setup_all` stops after preflight | A hard FAIL (FMAPI or catalog). | Resolve the blocker, or set `continue_on_error=true` to proceed and fix later. |
| A child step times out | `full` scale on a cold warehouse. | Re-run with `scale=demo`; increase the step timeout in `00_setup_all` STEPS. |
| Import error `pil_workshop` | Notebook not under the repo. | The bootstrap cell searches upward for `src/pil_workshop`; ensure the repo was cloned intact (Git Folders). |

## AI Gateway / FMAPI (01b, 08, app)

| Symptom | Cause | Fix |
|---|---|---|
| Preflight FAIL: *FMAPI endpoints* | Foundation Model APIs not enabled, or model not served in-region. | Enable FMAPI (pay-per-token); for out-of-region models enable **cross-geography routing** (Account Console → Settings → Feature enablement). Or set `PIL_TEXT_ENDPOINT`/`PIL_VISION_ENDPOINT`. |
| *Unity AI Gateway* WARN | Preview not enabled. | Endpoint-level config is used automatically; to centralize, enable the **Mosaic/Unity AI Gateway** preview (account admin). |
| Dashboard Page 4 empty | No AI traffic yet, or no system-tables access. | Run Part 2; ensure usage tracking is on (01b) and you have `system.serving.*` access. |
| `ai_query` errors on vision | No multimodal endpoint in-region. | Enable a multimodal FMAPI model / cross-geo routing; check the endpoint `llm.py` chose. |

## Data / Gold (02–04)

| Symptom | Cause | Fix |
|---|---|---|
| KPI smoke test OUT-OF-RANGE | Generator calibration changed. | Re-run 02–04; check `config.KPI_RANGES` and the generators. The `demo`/`full` presets are both tuned to pass. |
| Metric view `MEASURE()` fails | Metric views not available in region/preview. | Notebook 04 warns and continues; dashboards fall back to MVs. |
| Materialized view creation fails | MVs unavailable. | Notebook 04 auto-falls back to plain views; refresh is then on read. |

## Dashboard & Genie (05, 06)

| Symptom | Cause | Fix |
|---|---|---|
| Dashboard deploy fails | No serverless warehouse or Lakeview API change. | Assign a warehouse; else import `assets/dashboards/pil_operations.lvdash.json` via the UI and replace `${catalog}`. |
| Genie space not created via API | Preview/region gating. | Notebook 06 prints exact UI steps; use `assets/genie/space_config.yml`. |
| Genie answers are vague | Missing column comments. | Re-run notebook 03 (it comments every column) and re-sync the space. |

## App / Lakebase (09, 10)

| Symptom | Cause | Fix |
|---|---|---|
| App shows API-only placeholder | Frontend bundle not built. | `cd app/frontend && npm install && npm run build`, then re-sync/redeploy. |
| PDF/image 404 in app | App lacks READ VOLUME, or 07 not run. | Run notebook 07; grant the app READ VOLUME on `bronze.raw_invoices` + `bronze.container_images`. |
| Health shows `lakebase:false` | Lakebase not enabled or not granted. | Enable Lakebase; grant the app CAN CONNECT. The app still runs UC-only. |
| App model calls fail | SP lacks CAN QUERY on endpoints. | Grant the app's service principal CAN QUERY on the FMAPI text + vision endpoints. |

## ML (11, 12)

| Symptom | Cause | Fix |
|---|---|---|
| `%pip install` slow/first-run | lightgbm/ortools install. | Expected on first run; subsequent runs cache. |
| UC model registry fails | No CREATE MODEL on the `ml` schema. | Grant it, or the notebook logs metrics only and skips registration. |
| VRPTW `no_solution` | Time windows too tight. | Widen `time_windows` or increase `num_vehicles`. |

## Teardown (99)

| Symptom | Cause | Fix |
|---|---|---|
| "Teardown aborted — not confirmed" | Safety guard. | Set the `confirm` widget to the exact catalog name. |
| Genie space remains | Created via UI. | Delete it in the Genie UI (⋯ → Delete). |
