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

    ``workspace_client`` / ``sql_fn`` / ``write_fn`` are injected so the service
    is unit-testable with fakes. ``write_fn`` persists the extraction to Delta;
    when ``None`` the persist step is skipped (the extraction still returns).
    """

    def __init__(
        self,
        workspace_client: Any = None,
        sql_fn: Any = None,
        write_fn: Any = None,
        read_fn: Any = None,
    ) -> None:
        self._wc = workspace_client
        self._sql_fn = sql_fn
        self._write_fn = write_fn
        # Graceful read (returns [] if the sink is empty/missing) for the
        # "processed invoices" list; distinct from the strict extraction sql_fn.
        self._read_fn = read_fn

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
        """Run the extraction on one file; return rich structured data + metrics.

        Parses (``ai_parse_document``) and pulls flat fields (``ai_extract``)
        inline, then delegates the nested/line-item extraction to the governed
        UC function ``<catalog>.default.extract_invoice_fields``. The FMAPI
        endpoint is baked into that function (created by setup), so the app no
        longer passes an endpoint name here.
        """
        import time

        from pil_workshop.agent_bricks import build_single_invoice_extraction_sql

        if not self._sql_fn:
            raise RuntimeError(
                "Extraction needs a SQL warehouse. Grant the app CAN USE on a "
                "serverless warehouse (see app.yaml)."
            )
        catalog = get_settings().catalog
        sql = build_single_invoice_extraction_sql(catalog, volume_path)
        t0 = time.perf_counter()
        rows = list(self._sql_fn(sql))
        extract_ms = int((time.perf_counter() - t0) * 1000)
        if not rows:
            raise RuntimeError("Extraction returned no rows (parse may have failed).")
        row = rows[0]
        flat = _as_dict(row.get("flat"))
        nested = _parse_json(row.get("nested_json"))
        doc_chars = int(row.get("doc_chars") or 0)
        raw_json = row.get("nested_json") or ""
        metrics = self._metrics(doc_chars, str(raw_json), extract_ms, nested)
        return self._assemble(volume_path, flat, nested, metrics)

    def _metrics(
        self, doc_chars: int, raw_json: str, extract_ms: int, nested: dict[str, Any]
    ) -> dict[str, Any]:
        """Estimate tokens/cost for the governed ai_query call.

        ai_query exposes no usage metadata inline and the system usage tables lag
        ~a day, so tokens are ESTIMATED from character counts (~4 chars/token, a
        good approximation for English + the JSON we send/receive). The prompt
        instruction adds a fixed overhead on top of the parsed document text.
        Cost uses the same blended $/1M rate as the gold usage views.
        """
        input_chars = doc_chars + _INSTRUCTION_OVERHEAD_CHARS
        est_in = max(1, round(input_chars / _CHARS_PER_TOKEN))
        est_out = max(1, round(len(raw_json) / _CHARS_PER_TOKEN))
        est_total = est_in + est_out
        return {
            "extract_ms": extract_ms,
            "doc_chars": doc_chars,
            "est_input_tokens": est_in,
            "est_output_tokens": est_out,
            "est_total_tokens": est_total,
            "est_cost_usd": round(est_total / 1_000_000 * _COST_PER_M_TOKENS, 4),
            "field_count": _non_null_field_count(nested),
            "line_item_count": len(nested.get("line_items") or []),
        }

    def _assemble(
        self,
        volume_path: str,
        flat: dict[str, Any],
        nested: dict[str, Any],
        metrics: dict[str, Any],
    ) -> dict[str, Any]:
        import os

        # PO can arrive under the rich key (purchase_order) or the flat struct.
        po = _blank_to_none(
            nested.get("purchase_order") or flat.get("po_number")
        )
        subtotal = _num(nested.get("subtotal"))
        tax = _num(nested.get("tax")) or 0.0
        discount = _num(nested.get("discount")) or 0.0
        shipping = _num(nested.get("shipping")) or 0.0
        total = _num(nested.get("total")) or _num(flat.get("total"))
        line_items = nested.get("line_items") or []

        # Key fields whose absence means the extraction isn't usable and needs a
        # human — checked so an invoice with missing important values is routed
        # to the review queue, not just a missing PO.
        invoice_no = _blank_to_none(nested.get("invoice_no") or flat.get("invoice_no"))
        customer_name = _blank_to_none(nested.get("customer_name") or flat.get("customer"))
        currency = _norm_currency(nested.get("currency") or flat.get("currency"))
        missing = [
            name
            for name, val in (
                ("invoice_no", invoice_no),
                ("total", total),
                ("customer", customer_name),
                ("currency", currency),
            )
            if val is None
        ]

        # Exception priority (most severe first):
        #   1. total_mismatch — amounts present but don't reconcile (data integrity)
        #   2. missing_fields — a key field the workflow needs is absent
        #   3. missing_po     — PO specifically absent (freight convention)
        # A discount reduces the total whether the model returns it as +95 or
        # -95, so try both signs and accept if EITHER reconciles (small tolerance
        # + relative floor for large-value invoices).
        exception = None
        if total is not None and subtotal is not None:
            tol = max(1.0, abs(total) * 0.01)
            candidates = {
                subtotal + tax,  # simple invoices with no discount/shipping
                subtotal - discount + shipping + tax,
                subtotal + discount + shipping + tax,
            }
            if not any(abs(total - c) <= tol for c in candidates):
                exception = "total_mismatch"
        if exception is None and missing:
            exception = "missing_fields"
        if exception is None and po is None:
            exception = "missing_po"

        containers = nested.get("container_numbers") or []
        if isinstance(containers, str):
            containers = [containers]

        return {
            "file_name": os.path.basename(volume_path),
            "volume_path": volume_path,
            # header
            "invoice_no": nested.get("invoice_no") or flat.get("invoice_no"),
            "invoice_date": nested.get("invoice_date") or nested.get("date"),
            "due_date": nested.get("due_date"),
            "purchase_order": po,
            # parties
            "vendor_name": nested.get("vendor_name"),
            "vendor_tax_id": nested.get("vendor_tax_id"),
            "vendor_address": nested.get("vendor_address"),
            "customer_name": nested.get("customer_name") or flat.get("customer"),
            "customer_address": nested.get("customer_address"),
            # freight / shipping
            "currency": _norm_currency(nested.get("currency") or flat.get("currency")),
            "incoterms": nested.get("incoterms"),
            "bill_of_lading": nested.get("bill_of_lading"),
            "vessel_name": nested.get("vessel_name"),
            "container_numbers": [str(c) for c in containers if c],
            "port_of_loading": nested.get("port_of_loading"),
            "port_of_discharge": nested.get("port_of_discharge"),
            # terms
            "payment_terms": nested.get("payment_terms"),
            "bank_details": nested.get("bank_details"),
            "notes": nested.get("notes"),
            # money
            "subtotal": subtotal,
            "discount": _num(nested.get("discount")),
            "shipping": _num(nested.get("shipping")),
            "tax": tax,
            "tax_rate": _str_or_none(nested.get("tax_rate")),
            "total": total,
            "amount_paid": _num(nested.get("amount_paid")),
            "balance_due": _num(nested.get("balance_due")),
            "line_items": [
                {
                    "description": li.get("description"),
                    "quantity": _num(li.get("quantity")),
                    "unit_price": _num(li.get("unit_price")),
                    "amount": _num(li.get("amount")),
                }
                for li in line_items
                if isinstance(li, dict)
            ],
            "exception_type": exception,
            "metrics": {"model_endpoint": _model_endpoint(), **metrics},
        }

    # ---- 4. persist to Delta --------------------------------------------
    def _persist(self, result: dict[str, Any]) -> str | None:
        """Write one extraction row to the Delta sink; return the table or None.

        Best-effort: a write failure never fails the extraction response (the
        structured data is already computed). Uses a parameterized INSERT so
        arbitrary extracted text is safe; the array column is passed as a JSON
        string and parsed with ``from_json`` in SQL.
        """
        if not self._write_fn:
            return None
        from pil_workshop.agent_bricks import (
            INVOICE_UPLOAD_COLUMNS,
            invoice_uploads_table,
        )

        table = invoice_uploads_table(get_settings().catalog)
        # Map the assembled result to each column (result key = column, with a
        # few renames handled here).
        values = {
            "source_file": result.get("file_name"),
            "volume_path": result.get("volume_path"),
            "invoice_no": result.get("invoice_no"),
            "invoice_date": result.get("invoice_date"),
            "due_date": result.get("due_date"),
            "purchase_order": result.get("purchase_order"),
            "vendor_name": result.get("vendor_name"),
            "vendor_tax_id": result.get("vendor_tax_id"),
            "vendor_address": result.get("vendor_address"),
            "customer_name": result.get("customer_name"),
            "customer_address": result.get("customer_address"),
            "currency": result.get("currency"),
            "incoterms": result.get("incoterms"),
            "bill_of_lading": result.get("bill_of_lading"),
            "vessel_name": result.get("vessel_name"),
            "container_numbers": json.dumps(result.get("container_numbers") or []),
            "port_of_loading": result.get("port_of_loading"),
            "port_of_discharge": result.get("port_of_discharge"),
            "payment_terms": result.get("payment_terms"),
            "bank_details": result.get("bank_details"),
            "notes": result.get("notes"),
            "subtotal": result.get("subtotal"),
            "discount": result.get("discount"),
            "shipping": result.get("shipping"),
            "tax": result.get("tax"),
            "tax_rate": result.get("tax_rate"),
            "total": result.get("total"),
            "amount_paid": result.get("amount_paid"),
            "balance_due": result.get("balance_due"),
            "line_items_json": json.dumps(result.get("line_items") or []),
            "exception_type": result.get("exception_type"),
            "model_endpoint": (result.get("metrics") or {}).get("model_endpoint"),
            "est_total_tokens": (result.get("metrics") or {}).get("est_total_tokens"),
            "raw_json": json.dumps(result, default=str),
        }
        col_names = [c for c, _ in INVOICE_UPLOAD_COLUMNS]
        # container_numbers is ARRAY<STRING>: bind a JSON string, parse in SQL.
        placeholders = [
            "from_json(:container_numbers, 'ARRAY<STRING>')"
            if c == "container_numbers"
            else f":{c}"
            for c in col_names
        ]
        sql = (
            f"INSERT INTO {table} ({', '.join(f'`{c}`' for c in col_names)}) "
            f"VALUES ({', '.join(placeholders)})"
        )
        self._write_fn(sql, {c: values.get(c) for c in col_names})
        return table

    # ---- read back the processed list -----------------------------------
    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return recently processed invoices from the Delta sink (deduped).

        Dedup by ``source_file`` keeping the latest row (the table is
        append-only, so re-uploads add rows). Graceful: returns [] if the read
        fn is missing or the table isn't there yet.
        """
        if not self._read_fn:
            return []
        from pil_workshop.agent_bricks import invoice_uploads_table

        table = invoice_uploads_table(get_settings().catalog)
        lim = max(1, min(int(limit), 200))
        sql = f"""
            WITH ranked AS (
                SELECT source_file, invoice_no, customer_name, currency, total,
                       exception_type, extracted_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY source_file ORDER BY extracted_at DESC
                       ) AS rn
                FROM {table}
            )
            SELECT source_file, invoice_no, customer_name, currency, total,
                   exception_type, CAST(extracted_at AS STRING) AS extracted_at
            FROM ranked WHERE rn = 1
            ORDER BY extracted_at DESC LIMIT {lim}
        """
        try:
            rows = list(self._read_fn(sql))
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Recent-extractions read failed: %s", exc)
            return []
        return [
            {
                "source_file": r.get("source_file"),
                "invoice_no": r.get("invoice_no"),
                "customer_name": r.get("customer_name"),
                "currency": r.get("currency"),
                "total": _num(r.get("total")),
                "exception_type": r.get("exception_type"),
                "extracted_at": r.get("extracted_at"),
            }
            for r in rows
            if isinstance(r, dict)
        ]

    # ---- orchestration ---------------------------------------------------
    def process_upload(self, file_name: str, content: bytes) -> dict[str, Any]:
        """Full flow: save to Volume → extract → persist to Delta → output."""
        import time

        t0 = time.perf_counter()
        path = self.save_to_volume(file_name, content)
        save_ms = int((time.perf_counter() - t0) * 1000)
        result = self.extract(path)
        m = result.get("metrics") or {}
        m["save_ms"] = save_ms
        m["duration_ms"] = save_ms + int(m.get("extract_ms") or 0)
        result["metrics"] = m
        # Persist to Delta (best-effort — never fail the response on a write error).
        try:
            result["saved_table"] = self._persist(result)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Persist to Delta failed (extraction still returned): %s", exc)
            result["saved_table"] = None
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


def _str_or_none(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s or None


# Common currency symbol → ISO 4217 (the model is asked to return ISO, this is a
# safety net for stray symbols).
_CURRENCY_SYMBOLS = {
    "$": "USD", "£": "GBP", "€": "EUR", "¥": "JPY", "₹": "INR",
    "s$": "SGD", "sgd": "SGD", "rmb": "CNY", "元": "CNY",
}


def _norm_currency(v: Any) -> str | None:
    s = _blank_to_none(v)
    if s is None:
        return None
    key = s.strip().lower()
    if key in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[key]
    # a bare symbol embedded in a short string
    for sym, iso in _CURRENCY_SYMBOLS.items():
        if sym in s and len(s) <= 4:
            return iso
    # already an ISO-ish 3-letter code
    return s.upper() if len(s) == 3 and s.isalpha() else s


def _non_null_field_count(nested: dict[str, Any]) -> int:
    """Count populated top-level fields (line_items counted as one if present)."""
    n = 0
    for k, v in nested.items():
        if v in (None, "", [], {}):
            continue
        n += 1
    return n


def _model_endpoint() -> str | None:
    """Best-effort resolve the governed FMAPI endpoint name for display."""
    try:
        from backend.services.clients import text_endpoint_name

        return text_endpoint_name()
    except Exception:  # noqa: BLE001
        return None


# Token/cost estimation constants (ai_query exposes no inline usage; system
# usage tables lag ~a day, so we estimate and clearly label it "est.").
_CHARS_PER_TOKEN = 4.0
_INSTRUCTION_OVERHEAD_CHARS = 900  # the fixed prompt instruction we prepend
_COST_PER_M_TOKENS = 5.0  # same blended $/1M as gold.v_ai_usage_daily
