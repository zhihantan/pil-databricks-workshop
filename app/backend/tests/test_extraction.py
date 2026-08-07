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
    nested = {"purchase_order": "PO1", "customer_name": "Acme",
              "subtotal": 900.0, "tax": 100.0, "total": 1000.0,
              "line_items": [{"description": "Ocean Freight", "amount": 900.0,
                              "quantity": 1, "unit_price": 900.0}]}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/a.pdf")
    assert r["invoice_no"] == "INV-1"
    assert r["customer_name"] == "Acme"
    assert r["total"] == 1000.0 and r["exception_type"] is None
    assert len(r["line_items"]) == 1
    assert r["line_items"][0]["quantity"] == 1.0
    # metrics are always attached
    assert r["metrics"]["est_total_tokens"] > 0
    assert r["metrics"]["field_count"] > 0


def test_extract_flags_total_mismatch():
    flat = {"invoice_no": "INV-2", "customer": "X", "po_number": "PO2",
            "currency": "CNY", "total": "2,490.40 CNY"}
    nested = {"purchase_order": "PO2", "subtotal": 1753.38, "tax": 122.74,
              "total": 2490.4, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/b.pdf")
    # 2490.40 != 1753.38 + 122.74
    assert r["exception_type"] == "total_mismatch"


def test_extract_flags_missing_fields_when_key_field_absent():
    # PO present + totals reconcile, but customer is missing → missing_fields.
    flat = {"invoice_no": "INV-8", "po_number": "PO8", "currency": "USD",
            "total": "500.00"}
    nested = {"purchase_order": "PO8", "invoice_no": "INV-8", "currency": "USD",
              "subtotal": 500.0, "tax": 0.0, "total": 500.0, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/g.pdf")
    assert r["customer_name"] is None
    assert r["exception_type"] == "missing_fields"


def test_missing_fields_when_total_absent():
    flat = {"invoice_no": "INV-11", "customer": "Y", "po_number": "PO11", "currency": "USD"}
    nested = {"purchase_order": "PO11", "invoice_no": "INV-11", "customer_name": "Y",
              "currency": "USD", "subtotal": 100.0, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/h.pdf")
    assert r["total"] is None and r["exception_type"] == "missing_fields"


def test_total_mismatch_takes_priority_over_missing_fields():
    # customer missing AND totals don't reconcile → total_mismatch wins.
    flat = {"invoice_no": "INV-12", "currency": "USD", "total": "999.00"}
    nested = {"purchase_order": "PO12", "invoice_no": "INV-12", "currency": "USD",
              "subtotal": 100.0, "tax": 5.0, "total": 999.0, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/i.pdf")
    assert r["customer_name"] is None  # a key field is missing
    assert r["exception_type"] == "total_mismatch"  # but mismatch is more severe


def test_extract_flags_missing_po_placeholder():
    flat = {"invoice_no": "INV-3", "customer": "Y", "po_number": "—",
            "currency": "USD", "total": "500.00"}
    nested = {"purchase_order": "—", "subtotal": 500.0, "tax": 0.0,
              "total": 500.0, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/c.pdf")
    assert r["purchase_order"] is None and r["exception_type"] == "missing_po"


def test_discount_reconciles_without_false_mismatch():
    # subtotal 4295 - discount 95 + tax 378 = 4578 = total; must NOT flag.
    # All key fields present so this isolates the discount-reconciliation intent.
    flat = {"invoice_no": "INV-6", "customer": "Acme", "currency": "SGD",
            "total": "4,578.00"}
    nested = {"purchase_order": "PO6", "invoice_no": "INV-6", "customer_name": "Acme",
              "currency": "SGD", "subtotal": 4295.0, "discount": 95.0,
              "shipping": 0.0, "tax": 378.0, "total": 4578.0, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/f.pdf")
    assert r["exception_type"] is None
    assert r["discount"] == 95.0


def test_currency_symbol_normalized_to_iso():
    flat = {"invoice_no": "INV-5", "currency": "£", "total": "1,602.00"}
    nested = {"purchase_order": "PO5", "currency": "£", "subtotal": 1335.0,
              "tax": 267.0, "total": 1602.0, "line_items": []}
    svc = ExtractionService(sql_fn=_sql_fn_returning(flat, nested))
    r = svc.extract("/Volumes/c/bronze/raw_invoices/e.pdf")
    assert r["currency"] == "GBP"


def test_process_upload_saves_then_extracts_with_metrics():
    wc = _FakeWC()
    flat = {"invoice_no": "INV-4", "customer": "Z", "po_number": "PO4",
            "currency": "USD", "total": "10.00"}
    nested = {"purchase_order": "PO4", "subtotal": 10.0, "tax": 0.0,
              "total": 10.0, "line_items": []}
    svc = ExtractionService(
        workspace_client=wc, sql_fn=_sql_fn_returning(flat, nested)
    )
    r = svc.process_upload("d.pdf", b"%PDF bytes")
    assert r["invoice_no"] == "INV-4"
    assert any(p.endswith("/raw_invoices/d.pdf") for p in wc.files.saved)
    # duration_ms rolls up save + extract
    m = r["metrics"]
    assert m["duration_ms"] >= m["extract_ms"] and m["save_ms"] >= 0


def test_persist_writes_parameterized_insert_row():
    wc = _FakeWC()
    captured = {}

    def _write(sql, params):
        captured["sql"] = sql
        captured["params"] = params

    flat = {"invoice_no": "INV-9", "currency": "SGD", "total": "4,578.00"}
    nested = {
        "invoice_no": "INV-9", "purchase_order": "PO9", "currency": "SGD",
        "subtotal": 4295.0, "discount": 95.0, "tax": 378.0, "total": 4578.0,
        "container_numbers": ["PCIU1", "PCIU2"],
        "line_items": [{"description": "Ocean Freight", "amount": 4295.0}],
    }
    svc = ExtractionService(
        workspace_client=wc, sql_fn=_sql_fn_returning(flat, nested), write_fn=_write
    )
    r = svc.process_upload("z.pdf", b"%PDF")
    assert r["saved_table"].endswith("`invoice_extractions_app`")
    # write happened, parameterized (no literal values baked into the SQL)
    sql = captured["sql"]
    assert sql.startswith("INSERT INTO")
    assert ":invoice_no" in sql and ":total" in sql
    # array column parsed from a JSON-string param
    assert "from_json(:container_numbers, 'ARRAY<STRING>')" in sql
    p = captured["params"]
    assert p["invoice_no"] == "INV-9" and p["total"] == 4578.0
    assert json.loads(p["container_numbers"]) == ["PCIU1", "PCIU2"]
    assert json.loads(p["line_items_json"])[0]["description"] == "Ocean Freight"
    # raw_json round-trips the full result
    assert json.loads(p["raw_json"])["invoice_no"] == "INV-9"


def test_list_recent_maps_rows_and_coerces_total():
    captured = {}

    def _read(sql):
        captured["sql"] = sql
        return [
            {"source_file": "a.pdf", "invoice_no": "INV-1", "customer_name": "Acme",
             "currency": "USD", "total": "1,000.00", "exception_type": None,
             "extracted_at": "2026-08-03 09:00:00"},
            {"source_file": "b.pdf", "invoice_no": "INV-2", "customer_name": "X",
             "currency": "SGD", "total": 4578.0, "exception_type": "missing_po",
             "extracted_at": "2026-08-03 08:00:00"},
        ]

    svc = ExtractionService(read_fn=_read)
    rows = svc.list_recent(limit=10)
    assert len(rows) == 2
    assert rows[0]["source_file"] == "a.pdf" and rows[0]["total"] == 1000.0
    assert rows[1]["exception_type"] == "missing_po"
    # dedup + ordering happen in SQL (window function + ORDER BY)
    assert "ROW_NUMBER() OVER" in captured["sql"] and "LIMIT 10" in captured["sql"]


def test_list_recent_empty_without_read_fn():
    assert ExtractionService().list_recent() == []


def test_persist_skipped_when_no_write_fn():
    wc = _FakeWC()
    flat = {"invoice_no": "INV-10", "currency": "USD", "total": "5.00"}
    nested = {"purchase_order": "PO10", "subtotal": 5.0, "tax": 0.0, "total": 5.0,
              "line_items": []}
    svc = ExtractionService(workspace_client=wc, sql_fn=_sql_fn_returning(flat, nested))
    r = svc.process_upload("y.pdf", b"%PDF")
    assert r["saved_table"] is None  # no write_fn → persist skipped, still returns


def test_persist_self_heals_schema_and_table_then_inserts():
    """On a fresh workspace where notebook 08's sink step hasn't run, the persist
    creates the apps schema + sink (IF NOT EXISTS) before the INSERT — the INSERT
    is the LAST write."""
    wc = _FakeWC()
    writes: list = []
    flat = {"invoice_no": "INV-11", "currency": "USD", "total": "5.00"}
    nested = {"purchase_order": "PO11", "subtotal": 5.0, "tax": 0.0, "total": 5.0,
              "line_items": []}
    svc = ExtractionService(
        workspace_client=wc, sql_fn=_sql_fn_returning(flat, nested),
        write_fn=lambda sql, params: writes.append((sql, params)),
    )
    r = svc.process_upload("w.pdf", b"%PDF")
    assert r["saved_table"].endswith("`invoice_extractions_app`")
    assert writes[0][0].startswith("CREATE SCHEMA IF NOT EXISTS")
    assert "CREATE TABLE IF NOT EXISTS" in writes[1][0]
    assert writes[-1][0].startswith("INSERT INTO")


def test_persist_survives_self_heal_failure_and_still_inserts():
    """If the SP lacks CREATE (schema/table already exist), the self-heal error
    is swallowed and the INSERT still runs — the extraction is never lost."""
    wc = _FakeWC()
    inserts: list = []

    def _write(sql, params):
        if sql.strip().upper().startswith("CREATE"):
            raise RuntimeError("PERMISSION_DENIED: no CREATE on schema apps")
        inserts.append((sql, params))

    flat = {"invoice_no": "INV-12", "currency": "USD", "total": "5.00"}
    nested = {"purchase_order": "PO12", "subtotal": 5.0, "tax": 0.0, "total": 5.0,
              "line_items": []}
    svc = ExtractionService(
        workspace_client=wc, sql_fn=_sql_fn_returning(flat, nested), write_fn=_write
    )
    r = svc.process_upload("v.pdf", b"%PDF")
    assert r["saved_table"].endswith("`invoice_extractions_app`")
    assert len(inserts) == 1 and inserts[0][0].startswith("INSERT INTO")
