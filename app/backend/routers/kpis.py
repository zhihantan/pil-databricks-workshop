"""/api/kpis and /api/usage — home-page summary stats and AI usage widget."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from backend.deps import get_analytics_service
from backend.models.schemas import KpiSummary, UsageSummary
from backend.services.analytics_service import AnalyticsService

router = APIRouter(prefix="/api", tags=["analytics"])


@router.get("/kpis", response_model=KpiSummary)
def kpis(svc: AnalyticsService = Depends(get_analytics_service)) -> KpiSummary:
    """Summary KPIs for the app Home page."""
    return svc.kpi_summary()


@router.get("/usage", response_model=UsageSummary)
def usage(svc: AnalyticsService = Depends(get_analytics_service)) -> UsageSummary:
    """Today's governed-model token usage (from the AI Gateway usage views)."""
    return svc.usage_summary()
