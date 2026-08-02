"""/api/health — liveness + dependency status."""

from __future__ import annotations

from fastapi import APIRouter

from backend import __version__
from backend.core.config import get_settings
from backend.models.schemas import HealthResponse
from backend.services.clients import lakebase_available

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    settings = get_settings()
    lb = lakebase_available()
    return HealthResponse(
        status="ok" if lb else "degraded",
        lakebase=lb,
        catalog=settings.catalog,
        version=__version__,
    )
