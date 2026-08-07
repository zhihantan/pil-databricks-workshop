"""/api/health — liveness + dependency status."""

from __future__ import annotations

from fastapi import APIRouter

from backend import __version__
from backend.core.config import get_settings
from backend.models.schemas import HealthResponse
from backend.services.clients import lakebase_available, sql_connection_target

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    """Liveness + dependency status.

    Health reflects the CORE dependency (a SQL warehouse for UC reads +
    extraction), NOT Lakebase — UC-only (no Lakebase) is a fully-supported mode,
    so the app is 'ok' as long as the warehouse is reachable. Lakebase is
    reported as an informational sub-status only; its absence degrades review-
    queue persistence to the in-memory demo store but not the app overall.
    """
    settings = get_settings()
    lb = lakebase_available()
    warehouse_ok = sql_connection_target() is not None
    return HealthResponse(
        status="ok" if warehouse_ok else "degraded",
        lakebase=lb,
        catalog=settings.catalog,
        version=__version__,
    )
