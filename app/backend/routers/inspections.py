"""/api/inspections — list inspections, image preview, refresh analysis, work orders."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response

from backend.core.auth import current_user_email
from backend.core.config import get_settings
from backend.deps import get_inspection_service
from backend.models.schemas import InspectionItem, WorkOrderRequest
from backend.services.inspection_service import InspectionService

router = APIRouter(prefix="/api/inspections", tags=["inspections"])


@router.get("", response_model=list[InspectionItem])
def list_inspections(
    svc: InspectionService = Depends(get_inspection_service),
) -> list[InspectionItem]:
    """List container inspections with damage classification."""
    return svc.list_inspections()


@router.get("/{file_name}/image")
def get_image(file_name: str) -> Response:
    """Return the container image from the bronze Volume, if reachable."""
    settings = get_settings()
    base = f"/Volumes/{settings.catalog}/bronze/container_images"
    safe = os.path.basename(file_name)
    path = os.path.join(base, safe)
    if os.path.exists(path):
        return FileResponse(path, media_type="image/png")
    raise HTTPException(
        status_code=404,
        detail=f"Image not found at {path}. Run notebook 07 and grant the app "
        "READ VOLUME on bronze.container_images.",
    )


@router.post("/{file_name}/refresh")
def refresh(
    file_name: str,
    svc: InspectionService = Depends(get_inspection_service),
) -> dict:
    """Re-run vision analysis for one image via the governed vision endpoint."""
    settings = get_settings()
    base = f"/Volumes/{settings.catalog}/bronze/container_images"
    path = os.path.join(base, os.path.basename(file_name))
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="Image not available to re-analyze.")
    try:
        from pil_workshop import llm

        endpoints = llm.endpoints()
        with open(path, "rb") as fh:
            data = fh.read()
        result = svc.refresh_one(data, endpoints.vision, llm)
        return {"file_name": file_name, "result": result}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Vision endpoint call failed: {exc}. Ensure the app SP has "
            "CAN QUERY on the multimodal FMAPI endpoint.",
        ) from exc


@router.post("/work-order")
def create_work_order(
    req: WorkOrderRequest,
    svc: InspectionService = Depends(get_inspection_service),
    actor: str = Depends(current_user_email),
) -> dict:
    """Create an inspection work order → Lakebase (or in-memory demo)."""
    return svc.create_work_order(req, actor)
