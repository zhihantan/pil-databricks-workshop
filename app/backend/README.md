# PIL App — Backend (FastAPI)

Single FastAPI service for the PIL Invoice + Container Vision app. Serves the
API under `/api` and the built Vite bundle from `backend/static`.

## Modules

- `main.py` — app assembly, router mounting, SPA static serving.
- `routers/` — `invoices`, `inspections`, `kpis` (+ `usage`), `health`.
- `services/` — business logic, injected external clients:
  - `invoice_service` — Lakebase-backed review queue + decisions (falls back to
    an in-memory queue seeded from UC when Lakebase isn't connected).
  - `inspection_service` — inspection listing + single-image re-analysis through
    the governed vision endpoint (`pil_workshop.llm`) + work orders.
  - `analytics_service` — home KPIs + AI-usage widget from gold views.
  - `clients.py` — lazily-built SDK / SQL / Lakebase clients; degrade to None.
- `models/` — pydantic v2 schemas.
- `core/` — settings, logging, Databricks Apps auth headers.

## Governed model access

Every model call resolves its endpoint via `pil_workshop.llm` — the same
Unity-AI-Gateway-governed FMAPI endpoints as the notebooks. There are **no**
external provider SDKs or keys. App traffic therefore shows up on Dashboard
Page 4 alongside notebook traffic.

## Local development

```bash
cd app
python -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
# API-only (frontend proxied separately by Vite):
PIL_DEV_CORS=1 uvicorn backend.main:app --reload --port 8000
```

The app runs **without** Databricks/Lakebase locally: services fall back to
sample/in-memory data so every page is clickable. Set `PIL_CATALOG`,
`PIL_WAREHOUSE_ID`, `PIL_LAKEBASE_INSTANCE` to point at real resources.

## Tests

```bash
cd app
PYTHONPATH=.:../src pytest backend/tests -q
```

Service tests mock external clients; API tests use FastAPI's `TestClient` with
`dependency_overrides`.

## Deploy (Databricks Apps)

See `../app.yaml` and `setup/10_deploy_app.py`. The app runs as its service
principal; grant it CAN USE (warehouse), CAN QUERY (FMAPI text + vision
endpoints), CAN CONNECT (Lakebase), and READ VOLUME (invoice/image volumes).
