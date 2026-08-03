"""Unit tests for ExtractionService (upload → save → extract → structured out).

Uses fakes for the workspace Files client and the SQL function so the pure
assembly/exception logic is tested off-platform.
"""

from __future__ import annotations

import json

from backend.services.extraction_service import (
    ExtractionService,
    _blank_to_none,
    _num,
    _parse_json,
)


class _FakeFiles:
    def __init__(self):
        self.saved = {}

    def upload(self, path, buf, overwrite=False):  # noqa: ANN001
        self.saved[path] = buf.read()


class _FakeWC:
    def __init__(self):
        self.files = _FakeFiles()


def _sql_fn_returning(flat: dict, nested: dict):
    def _fn(_sql):
        return [{"flat": flat, "nested_json": json.dumps(nested)}]

    return _fn


# --- helpers -----------------------------------------------------------------
def test_num_parses_currency_strings():
    assert _num("2,490.40 CNY") == 2490.4
    assert _num(1876.12) == 1876.12
    assert _num(None) is None


def test_blank_to_none_handles_placeholders():
    assert _blank_to_none("—") is None
    assert _blank_to_none("n/a") is None
    assert _blank_to_none("PO200058") == "PO200058"


def test_parse_json_strips_prose():
    assert _parse_json('here is your data: {"total": 5} thanks')["total"] == 5
    assert _parse_json("not json") == {}


# --- save-to-volume ----------------------------------------------------------
def test_save_to_volume_writes_pdf_path():
    wc = _FakeWC()
    svc = ExtractionService(workspace_client=wc)
    path = svc.save_to_volume("../etc/inv.pdf", b"%PDF-1.4 bytes")
    # basename only, under the governed volume path
    assert path.endswith("/bronze/raw_invoices/inv.pdf")
    assert "/Volumes/" in path
    assert wc.files.saved[path] == b"%PDF-1.4 bytes"


# --- extraction assembly + exception derivation ------------------------------
def test_extract_clean_invoice_no_exception():
    flat = {"invoice_no": "INV-1", "customer": "Acme", "po_number": "PO1",
            "currency": "USD", "total": "1,000.00 USD"}
    nested = {"subtotal": 900.0, "tax": 100.0, "total": 1000.0,
              "line_items": [{"description": "Ocean Freight", "amount": 900.0}]}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/a.pdf")
    assert r["invoice_no"] == "INV-1"
    assert r["total"] == 1000.0 and r["exception_type"] is None
    assert len(r["line_items"]) == 1


def test_extract_flags_total_mismatch():
    flat = {"invoice_no": "INV-2", "customer": "X", "po_number": "PO2",
            "currency": "CNY", "total": "2,490.40 CNY"}
    nested = {"subtotal": 1753.38, "tax": 122.74, "total": 2490.4, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/b.pdf")
    # 2490.40 != 1753.38 + 122.74
    assert r["exception_type"] == "total_mismatch"


def test_extract_flags_missing_po_placeholder():
    flat = {"invoice_no": "INV-3", "customer": "Y", "po_number": "—",
            "currency": "USD", "total": "500.00"}
    nested = {"subtotal": 500.0, "tax": 0.0, "total": 500.0, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/c.pdf")
    assert r["po_number"] is None and r["exception_type"] == "missing_po"


def test_process_upload_saves_then_extracts():
    wc = _FakeWC()
    flat = {"invoice_no": "INV-4", "customer": "Z", "po_number": "PO4",
            "currency": "USD", "total": "10.00"}
    nested = {"subtotal": 10.0, "tax": 0.0, "total": 10.0, "line_items": []}
    svc = ExtractionService(
        workspace_client=wc, sql_fn=_sql_fn_returning(flat, nested)
    )
    r = svc.process_upload("d.pdf", b"%PDF bytes")
    assert r["invoice_no"] == "INV-4"
    assert any(p.endswith("/raw_invoices/d.pdf") for p in wc.files.saved)
