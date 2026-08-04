"""/api/inspections — list inspections, image preview, refresh analysis, work orders."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response

from backend.core.auth import current_user_email
from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.deps import get_inspection_service
from backend.models.schemas import (
    ContainerAnalysis,
    InspectionAccuracy,
    InspectionItem,
    WorkOrderRequest,
)
from backend.services.clients import workspace_client
from backend.services.inspection_service import InspectionService

router = APIRouter(prefix="/api/inspections", tags=["inspections"])

_MAX_IMAGE_BYTES = 15 * 1024 * 1024  # 15 MB


def _read_volume_image(file_name: str) -> tuple[bytes, str] | None:
    """Return (bytes, media_type) for a container image, or None if unreachable.

    Databricks Apps run in a container where UC Volumes are NOT filesystem-
    mounted, so read via the Files API (same as the invoice PDF preview). A
    local filesystem read is tried first for local dev.
    """
    settings = get_settings()
    safe = os.path.basename(file_name)
    media = "image/jpeg" if safe.lower().endswith((".jpg", ".jpeg")) else "image/png"
    vol_path = f"/Volumes/{settings.catalog}/bronze/container_images/{safe}"
    # Fast path: local filesystem (dev / fuse-mounted).
    if os.path.exists(vol_path):
        with open(vol_path, "rb") as fh:
            return fh.read(), media
    # Real path in Apps: Files API.
    wc = workspace_client()
    if wc is not None:
        try:
            return wc.files.download(vol_path).contents.read(), media
        except Exception as exc:  # noqa: BLE001
            get_logger("backend.inspections").info("image fetch failed for %s: %s", safe, exc)
    return None


@router.get("", response_model=list[InspectionItem])
def list_inspections(
    svc: InspectionService = Depends(get_inspection_service),
) -> list[InspectionItem]:
    """List container inspections with damage classification."""
    return svc.list_inspections()


@router.get("/accuracy", response_model=InspectionAccuracy)
def accuracy(
    svc: InspectionService = Depends(get_inspection_service),
) -> InspectionAccuracy:
    """Vision-agent accuracy vs labelled ground truth (+ confusion counts)."""
    return InspectionAccuracy(**svc.accuracy_summary())


@router.post("/upload", response_model=ContainerAnalysis)
async def upload_and_analyze(
    file: UploadFile = File(...),
    svc: InspectionService = Depends(get_inspection_service),
) -> ContainerAnalysis:
    """Upload a container image → save to the volume → analyze via the governed
    vision endpoint → return damage/type/confidence/action + run metrics."""
    name = file.filename or "upload.png"
    if not name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        raise HTTPException(status_code=400, detail="Only PNG/JPG/WEBP images are supported.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > _MAX_IMAGE_BYTES:
        raise HTTPException(status_code=413, detail="Image exceeds the 15 MB limit.")
    try:
        from pil_workshop import llm

        endpoints = llm.endpoints()
        result = svc.save_and_analyze(name, content, endpoints.vision, llm)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Analysis failed: {exc}. Ensure the app can write to the "
            "container_images Volume and query the governed vision endpoint.",
        ) from exc
    return ContainerAnalysis(**result)


@router.get("/{file_name}/image")
def get_image(file_name: str) -> Response:
    """Stream a container image from the bronze Volume for inline preview."""
    img = _read_volume_image(file_name)
    if img is None:
        raise HTTPException(
            status_code=404,
            detail=f"Image {os.path.basename(file_name)} not found. Run notebook 07 "
            "and grant the app READ VOLUME on bronze.container_images.",
        )
    data, media = img
    return Response(
        content=data,
        media_type=media,
        headers={
            "Content-Disposition": f'inline; filename="{os.path.basename(file_name)}"',
            "Cache-Control": "public, max-age=3600",
        },
    )


@router.post("/{file_name}/refresh")
def refresh(
    file_name: str,
    svc: InspectionService = Depends(get_inspection_service),
) -> dict:
    """Re-run vision analysis for one image via the governed vision endpoint."""
    img = _read_volume_image(file_name)
    if img is None:
        raise HTTPException(status_code=404, detail="Image not available to re-analyze.")
    try:
        from pil_workshop import llm

        endpoints = llm.endpoints()
        data, _ = img
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
