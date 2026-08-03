"""Invoice upload + extraction service.

Implements the app's document-processing flow: an uploaded PDF is saved to the
same governed Volume the workshop uses (`bronze/raw_invoices`), parsed and
extracted with the same AI-function pipeline as notebook 08
(`ai_parse_document` + `ai_extract` + `ai_query`), and returned as structured
data with a derived exception flag.

All model traffic goes through the governed FMAPI endpoint resolved by
``pil_workshop.llm`` — the same one the notebooks and the rest of the app use.
"""

from __future__ import annotations

import json
import re
from typing import Any

from backend.core.config import get_settings
from backend.core.logging import get_logger

LOG = get_logger("backend.extraction_service")

_VOLUME_SUBPATH = "bronze/raw_invoices"


class ExtractionService:
    """Save an uploaded invoice to the Volume and extract structured data.

    ``workspace_client`` / ``sql_fn`` are injected so the service is
    unit-testable with fakes.
    """

    def __init__(
        self,
        workspace_client: Any = None,
        sql_fn: Any = None,
    ) -> None:
        self._wc = workspace_client
        self._sql_fn = sql_fn

    # ---- volume path helpers --------------------------------------------
    def _volume_path(self, file_name: str) -> str:
        settings = get_settings()
        import os

        safe = os.path.basename(file_name)  # strip any path components
        if not safe.lower().endswith(".pdf"):
            safe += ".pdf"
        return f"/Volumes/{settings.catalog}/{_VOLUME_SUBPATH}/{safe}", safe

    # ---- 1. save to volume ----------------------------------------------
    def save_to_volume(self, file_name: str, content: bytes) -> str:
        """Upload the PDF bytes to the governed Volume; return the volume path."""
        path, _ = self._volume_path(file_name)
        if self._wc is None:
            raise RuntimeError(
                "No Databricks workspace client; cannot write to the Volume."
            )
        import io

        # Files API overwrites so re-uploading the same name is idempotent.
        self._wc.files.upload(path, io.BytesIO(content), overwrite=True)
        LOG.info("Saved upload to volume: %s (%d bytes)", path, len(content))
        return path

    # ---- 2+3. extract + derive exception --------------------------------
    def extract(self, volume_path: str) -> dict[str, Any]:
        """Run the extraction on one file; return structured data.

        Parses (``ai_parse_document``) and pulls flat fields (``ai_extract``)
        inline, then delegates the nested/line-item extraction to the governed
        UC function ``<catalog>.default.extract_invoice_fields``. The FMAPI
        endpoint is baked into that function (created by setup), so the app no
        longer passes an endpoint name here.
        """
        from pil_workshop.agent_bricks import build_single_invoice_extraction_sql

        if not self._sql_fn:
            raise RuntimeError(
                "Extraction needs a SQL warehouse. Grant the app CAN USE on a "
                "serverless warehouse (see app.yaml)."
            )
        catalog = get_settings().catalog
        sql = build_single_invoice_extraction_sql(catalog, volume_path)
        rows = list(self._sql_fn(sql))
        if not rows:
            raise RuntimeError("Extraction returned no rows (parse may have failed).")
        row = rows[0]
        flat = _as_dict(row.get("flat"))
        nested = _parse_json(row.get("nested_json"))
        return self._assemble(volume_path, flat, nested)

    def _assemble(
        self, volume_path: str, flat: dict[str, Any], nested: dict[str, Any]
    ) -> dict[str, Any]:
        import os

        po = _blank_to_none(flat.get("po_number") or nested.get("po_number"))
        subtotal = _num(nested.get("subtotal"))
        tax = _num(nested.get("tax")) or 0.0
        total = _num(nested.get("total")) or _num(flat.get("total"))
        line_items = nested.get("line_items") or []

        exception = None
        if total is not None and subtotal is not None:
            if abs(total - (subtotal + tax)) > 1.0:
                exception = "total_mismatch"
        if exception is None and po is None:
            exception = "missing_po"

        return {
            "file_name": os.path.basename(volume_path),
            "volume_path": volume_path,
            "invoice_no": flat.get("invoice_no") or nested.get("invoice_no"),
            "customer": flat.get("customer") or nested.get("customer"),
            "po_number": po,
            "currency": flat.get("currency") or nested.get("currency"),
            "date": nested.get("date"),
            "payment_terms": nested.get("payment_terms"),
            "subtotal": subtotal,
            "tax": tax,
            "total": total,
            "line_items": [
                {"description": li.get("description"), "amount": _num(li.get("amount"))}
                for li in line_items
                if isinstance(li, dict)
            ],
            "exception_type": exception,
        }

    # ---- orchestration ---------------------------------------------------
    def process_upload(self, file_name: str, content: bytes) -> dict[str, Any]:
        """Full flow: save to Volume → extract → structured output."""
        path = self.save_to_volume(file_name, content)
        result = self.extract(path)
        return result


# --------------------------------------------------------------------------
# helpers (module-level so they're unit-testable)
# --------------------------------------------------------------------------
def _as_dict(v: Any) -> dict[str, Any]:
    if v is None:
        return {}
    if isinstance(v, dict):
        return v
    if hasattr(v, "asDict"):
        return v.asDict()
    try:
        return dict(v)
    except Exception:  # noqa: BLE001
        return {}


def _parse_json(raw: Any) -> dict[str, Any]:
    """Extract the JSON object from a model response that may wrap it in prose."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    m = re.search(r"\{.*\}", str(raw), re.S)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except (ValueError, TypeError):
        return {}


def _num(v: Any) -> float | None:
    """Coerce a value like '2,490.40 CNY' or 2490.4 to a float, else None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"-?[0-9]+(?:\.[0-9]+)?", str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def _blank_to_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return None if s.lower() in ("", "—", "-", "n/a", "none", "null") else s
