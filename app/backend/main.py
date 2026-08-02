"""FastAPI application entry point for the PIL Databricks App.

Serves the API under ``/api`` and the built Vite SPA from ``backend/static``
(with a catch-all so client-side routes deep-link correctly). Run locally with:

    uvicorn backend.main:app --reload

On Databricks Apps, ``app.yaml`` runs the same command on port 8000.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend.core.config import get_settings
from backend.core.logging import configure_logging, get_logger
from backend.routers import health, inspections, invoices, kpis

configure_logging()
LOG = get_logger("backend.main")

app = FastAPI(
    title="PIL Invoice & Container Vision",
    version=__version__,
    description="Databricks App: invoice review (Lakebase) + container inspections "
    "(governed FMAPI vision). All model traffic is Unity-AI-Gateway governed.",
)

# CORS is permissive only for local dev; on Databricks the app is same-origin.
if os.environ.get("PIL_DEV_CORS") == "1":
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

for r in (health.router, invoices.router, inspections.router, kpis.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def _unhandled(_request, exc):  # noqa: ANN001
    LOG.exception("Unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "internal error"})


# ---------------------------------------------------------------------------
# Static SPA. Mounted last so /api routes win. If the bundle isn't built yet
# (local API-only dev), a helpful placeholder is returned at "/".
# ---------------------------------------------------------------------------
_settings = get_settings()
_static_dir = _settings.static_dir

if os.path.isdir(_static_dir) and os.path.exists(os.path.join(_static_dir, "index.html")):
    app.mount("/assets", StaticFiles(directory=os.path.join(_static_dir, "assets")),
              name="assets")

    @app.get("/{full_path:path}")
    async def spa(full_path: str):  # noqa: ANN001
        # Serve real files if present; otherwise index.html for client routing.
        candidate = os.path.join(_static_dir, full_path)
        if full_path and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(_static_dir, "index.html"))
else:
    @app.get("/")
    async def _placeholder():
        return JSONResponse({
            "app": "PIL Invoice & Container Vision",
            "note": "Frontend bundle not built. Run `npm install && npm run build` "
                    "in app/frontend to populate backend/static.",
            "api_docs": "/docs",
        })
