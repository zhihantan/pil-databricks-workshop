"""/api/invoices — review queue, extraction detail, PDF preview, decisions."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response

from backend.core.auth import current_user_email
from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.deps import get_extraction_service, get_invoice_service
from backend.models.schemas import (
    ExtractedInvoice,
    InvoiceDecisionRequest,
    InvoiceQueueItem,
    ProcessedInvoice,
)
from backend.services.clients import workspace_client
from backend.services.extraction_service import ExtractionService
from backend.services.invoice_service import InvoiceService

router = APIRouter(prefix="/api/invoices", tags=["invoices"])

# Guardrails for uploads.
_MAX_UPLOAD_BYTES = 15 * 1024 * 1024  # 15 MB


@router.get("", response_model=list[InvoiceQueueItem])
def list_queue(
    status: str | None = None,
    svc: InvoiceService = Depends(get_invoice_service),
) -> list[InvoiceQueueItem]:
    """List the invoice review queue, optionally filtered by status."""
    return svc.list_queue(status=status)


@router.get("/extractions", response_model=list[ProcessedInvoice])
def list_processed(
    limit: int = 50,
    svc: ExtractionService = Depends(get_extraction_service),
) -> list[ProcessedInvoice]:
    """Recently processed invoices (from the Delta sink), latest first, deduped."""
    return [ProcessedInvoice(**r) for r in svc.list_recent(limit=limit)]


@router.post("/upload", response_model=ExtractedInvoice)
async def upload_and_extract(
    file: UploadFile = File(...),
    svc: ExtractionService = Depends(get_extraction_service),
    invoices: InvoiceService = Depends(get_invoice_service),
) -> ExtractedInvoice:
    """Upload a PDF invoice → save to the governed Volume → parse + extract →
    persist to Delta → (if flagged) enqueue for human review → return data.
    """
    name = file.filename or "upload.pdf"
    if not name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF invoices are supported.")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 15 MB limit.")
    try:
        result = svc.process_upload(name, content)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"Extraction failed: {exc}. Ensure the app can write to the "
            "invoice Volume and query the governed FMAPI endpoint.",
        ) from exc

    # Reverse-ETL loop: a flagged extraction goes to the Lakebase review queue
    # (the operational/OLTP store) for a human decision. Best-effort — never
    # fail the extraction response on an enqueue error.
    if result.get("exception_type"):
        try:
            result["queued_for_review"] = invoices.enqueue_for_review(
                file_name=result.get("file_name"),
                invoice_no=result.get("invoice_no"),
                customer=result.get("customer_name"),
                extracted_total=result.get("total"),
                exception_type=result.get("exception_type"),
                po_number=result.get("purchase_order"),
                currency=result.get("currency"),
            ) is not None
        except Exception as exc:  # noqa: BLE001
            result["queued_for_review"] = False
            # log-only; extraction already succeeded
            import logging

            logging.getLogger("backend.invoices").warning("enqueue failed: %s", exc)
    return ExtractedInvoice(**result)


@router.get("/{file_name}/pdf")
def get_pdf(file_name: str) -> Response:
    """Stream an invoice PDF from the bronze Volume for inline preview.

    Databricks Apps run in a container where UC Volumes are NOT filesystem-
    mounted, so read the bytes via the Files API (the same API the upload flow
    uses to write). A local filesystem read is tried first for local dev where
    the Volume may be mounted. Served inline so the browser renders it in-page.
    """
    settings = get_settings()
    safe = os.path.basename(file_name)
    vol_path = f"/Volumes/{settings.catalog}/bronze/raw_invoices/{safe}"
    inline = {"Content-Disposition": f'inline; filename="{safe}"'}

    # Fast path: local filesystem (dev, or if the Volume is fuse-mounted).
    local = os.path.join(f"/Volumes/{settings.catalog}/bronze/raw_invoices", safe)
    if os.path.exists(local):
        return FileResponse(local, media_type="application/pdf", headers=inline)

    # Real path in Apps: fetch bytes through the Files API.
    wc = workspace_client()
    if wc is not None:
        try:
            resp = wc.files.download(vol_path)
            data = resp.contents.read()
            return Response(content=data, media_type="application/pdf", headers=inline)
        except Exception as exc:  # noqa: BLE001
            get_logger("backend.invoices").info("PDF fetch failed for %s: %s", safe, exc)

    raise HTTPException(
        status_code=404,
        detail=f"PDF not found at {vol_path}. Run notebook 07 to generate "
        "invoices, and grant the app READ VOLUME on bronze.raw_invoices.",
    )


@router.post("/{file_name}/decision")
def decide(
    file_name: str,
    req: InvoiceDecisionRequest,
    svc: InvoiceService = Depends(get_invoice_service),
    actor: str = Depends(current_user_email),
) -> dict:
    """Record an approve/reject/adjust decision → Lakebase (or in-memory)."""
    return svc.decide(file_name, req, actor)
