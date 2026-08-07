"""Unit tests for the service layer with mocked external clients.

These run off-platform (no Databricks/Lakebase) and exercise the fallback paths
plus the decision/work-order logic. Run: ``pytest app/backend/tests``.
"""

from __future__ import annotations

from backend.models.schemas import InvoiceDecisionRequest, WorkOrderRequest
from backend.services.analytics_service import AnalyticsService
from backend.services.inspection_service import InspectionService
from backend.services.invoice_service import InvoiceService


def _fake_sql(rows):
    """Return a sql_fn that always yields ``rows`` regardless of query."""

    def _fn(_sql):
        return list(rows)

    return _fn


# --------------------------------------------------------------------------
# InvoiceService
# --------------------------------------------------------------------------
def test_invoice_queue_falls_back_to_sample_when_no_backend():
    svc = InvoiceService(conn_factory=None, sql_fn=None)
    items = svc.list_queue()
    assert len(items) >= 1
    assert all(i.pdf_preview_url.startswith("/api/invoices/") for i in items)


def test_invoice_queue_seeds_from_uc():
    rows = [
        {"file_name": "invoice_0001.pdf", "invoice_no": "INV-1", "customer": "Acme",
         "extracted_total": 100.0, "ground_truth_total": 90.0,
         "exception_type": "total_mismatch"},
    ]
    svc = InvoiceService(conn_factory=None, sql_fn=_fake_sql(rows))
    items = svc.list_queue()
    assert items[0].file_name == "invoice_0001.pdf"
    assert items[0].exception_type == "total_mismatch"


def test_invoice_queue_falls_back_to_ground_truth_when_exceptions_missing():
    """Regression (the real-workspace case): when gold.invoice_exceptions doesn't
    exist (08 extraction never ran/failed), seed from silver.invoice_pdf_ground_truth
    — whose file_name→customer is correct-by-construction — NOT the static sample.
    """

    def _routed_sql(sql):
        if "invoice_exceptions" in sql:
            raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND: gold.invoice_exceptions")
        if "invoice_pdf_ground_truth" in sql:
            assert "gt_anomaly IS NOT NULL" in sql  # flagged-only
            return [
                {"file_name": "invoice_0058.pdf", "invoice_no": "INV-2026-100058",
                 "customer": "Kirin Foods Trading", "extracted_total": 2490.4,
                 "ground_truth_total": 2490.4, "exception_type": "total_mismatch"},
            ]
        return []

    svc = InvoiceService(conn_factory=None, sql_fn=_routed_sql)
    items = svc.list_queue()
    assert len(items) == 1
    assert items[0].file_name == "invoice_0058.pdf"
    assert items[0].customer == "Kirin Foods Trading"  # matches the real PDF
    assert items[0].exception_type == "total_mismatch"


def test_invoice_queue_static_sample_only_when_no_uc_tables():
    """When BOTH UC sources are unreachable, fall back to the static sample."""

    def _all_missing(sql):
        raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND")

    svc = InvoiceService(conn_factory=None, sql_fn=_all_missing)
    items = svc.list_queue()
    # static sample is non-empty and every row points at a real PDF path
    assert len(items) >= 1
    assert all(i.pdf_preview_url.startswith("/api/invoices/") for i in items)


class _FakeCursor:
    """Minimal DB-API cursor over a fixed list of queue rows (dicts)."""

    _COLS = ("id", "file_name", "invoice_no", "customer", "po_number", "currency",
             "extracted_total", "ground_truth_total", "exception_type", "status")

    def __init__(self, store):
        self._store = store
        self._result: list[tuple] = []
        self._scalar = False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        if "LIMIT 1" in sql:  # the _table_is_empty probe
            self._scalar = True
            self._result = [(1,)] if self._store else []
            return
        self._scalar = False
        rows = self._store
        if params:  # status filter
            rows = [r for r in rows if r.get("status") == params[0]]
        self._result = [tuple(r.get(c) for c in self._COLS) for r in rows]

    @property
    def description(self):
        return [(c,) for c in self._COLS]

    def fetchall(self):
        return self._result

    def fetchone(self):
        return self._result[0] if self._result else None


class _FakeConn:
    def __init__(self, store):
        self._store = store

    def cursor(self):
        return _FakeCursor(self._store)

    def close(self):
        pass


def test_lakebase_unseeded_queue_seeds_demo_view_from_uc():
    """Regression: a provisioned-but-EMPTY Lakebase queue (09 couldn't seed it)
    must show the real flagged invoices from UC, not a blank screen."""
    gt_rows = [
        {"file_name": "invoice_0058.pdf", "invoice_no": "INV-2026-100058",
         "customer": "Kirin Foods Trading", "extracted_total": 2490.4,
         "ground_truth_total": 2490.4, "exception_type": "total_mismatch"},
    ]

    def _routed_sql(sql):
        if "invoice_exceptions" in sql:
            raise RuntimeError("TABLE_OR_VIEW_NOT_FOUND")
        if "invoice_pdf_ground_truth" in sql:
            return gt_rows
        return []

    svc = InvoiceService(conn_factory=lambda: _FakeConn([]), sql_fn=_routed_sql)
    items = svc.list_queue()
    assert [i.file_name for i in items] == ["invoice_0058.pdf"]
    assert items[0].customer == "Kirin Foods Trading"


def test_lakebase_all_decided_stays_empty_not_reseeded():
    """Regression (protects bug #1 on the Lakebase path): once the queue has been
    seeded and all rows decided, a pending query returns EMPTY — it must NOT
    re-seed the demo view (which would resurrect decided invoices)."""
    seeded = [
        {"id": 1, "file_name": "a.pdf", "invoice_no": "INV-1", "customer": "X",
         "po_number": None, "currency": "USD", "extracted_total": 1.0,
         "ground_truth_total": 1.0, "exception_type": "missing_po", "status": "approved"},
    ]

    def _sql_should_not_be_used(sql):
        raise AssertionError("must not fall back to UC when table is non-empty")

    svc = InvoiceService(conn_factory=lambda: _FakeConn(seeded),
                        sql_fn=_sql_should_not_be_used)
    # No pending rows, but the table is non-empty → stays empty, no re-seed.
    assert svc.list_queue(status="pending") == []


def test_invoice_status_filter_and_decision_updates_memory():
    rows = [
        {"file_name": "a.pdf", "invoice_no": "1", "customer": "X",
         "extracted_total": 1.0, "ground_truth_total": 1.0, "exception_type": "missing_po"},
        {"file_name": "b.pdf", "invoice_no": "2", "customer": "Y",
         "extracted_total": 2.0, "ground_truth_total": 2.0, "exception_type": "missing_po"},
    ]
    svc = InvoiceService(conn_factory=None, sql_fn=_fake_sql(rows))
    assert len(svc.list_queue(status="pending")) == 2
    out = svc.decide("a.pdf", InvoiceDecisionRequest(decision="approved"), actor="tester")
    assert out["decision"] == "approved"
    assert len(svc.list_queue(status="pending")) == 1
    assert len(svc.list_queue(status="approved")) == 1


def test_invoice_decision_persists_across_fresh_service_instances():
    """Regression: services are per-request, so a decision must survive to the
    next request via the shared demo store (not per-instance state)."""
    rows = [
        {"file_name": "a.pdf", "invoice_no": "1", "customer": "X",
         "extracted_total": 1.0, "ground_truth_total": 1.0, "exception_type": "missing_po"},
    ]
    # First "request" seeds + decides.
    InvoiceService(sql_fn=_fake_sql(rows)).list_queue()
    InvoiceService(sql_fn=_fake_sql(rows)).decide(
        "a.pdf", InvoiceDecisionRequest(decision="rejected"), actor="t")
    # A brand-new service instance ("next request") sees the rejection.
    fresh = InvoiceService(sql_fn=_fake_sql(rows))
    assert fresh.list_queue(status="pending") == []
    assert len(fresh.list_queue(status="rejected")) == 1


def test_enqueue_for_review_demo_fallback_shows_in_queue():
    from backend.services import demo_store

    demo_store.reset()
    svc = InvoiceService(conn_factory=None, sql_fn=_fake_sql([]))
    backing = svc.enqueue_for_review(
        file_name="up.pdf", invoice_no="INV-X", customer="Acme",
        extracted_total=500.0, exception_type="missing_po")
    assert backing == "memory"
    # a fresh instance (next request) sees the flagged item pending
    fresh = InvoiceService(conn_factory=None, sql_fn=_fake_sql([]))
    pend = fresh.list_queue(status="pending")
    assert any(i.file_name == "up.pdf" and i.exception_type == "missing_po" for i in pend)


def test_decide_applies_corrections_and_resolves():
    from backend.services import demo_store

    demo_store.reset()
    svc = InvoiceService(conn_factory=None, sql_fn=_fake_sql([]))
    svc.enqueue_for_review(
        file_name="fix.pdf", invoice_no="INV-Z", customer="Acme",
        extracted_total=500.0, exception_type="missing_po")
    # Reviewer supplies the missing PO and approves.
    out = svc.decide(
        "fix.pdf",
        InvoiceDecisionRequest(decision="approved", corrections={"po_number": "PO-9911"}),
        actor="tester")
    assert out["decision"] == "approved"
    assert out["corrections_applied"]["po_number"] == "PO-9911"
    # No longer pending; the correction stuck on the row.
    fresh = InvoiceService(conn_factory=None, sql_fn=_fake_sql([]))
    assert not any(i.file_name == "fix.pdf" for i in fresh.list_queue(status="pending"))
    approved = [i for i in fresh.list_queue(status="approved") if i.file_name == "fix.pdf"]
    assert approved and approved[0].po_number == "PO-9911"


def test_decide_corrections_whitelist_ignores_unknown_fields():
    from backend.services import demo_store

    demo_store.reset()
    svc = InvoiceService(conn_factory=None, sql_fn=_fake_sql([]))
    svc.enqueue_for_review("w.pdf", "INV-W", "X", 10.0, "missing_fields")
    out = svc.decide(
        "w.pdf",
        InvoiceDecisionRequest(
            decision="approved",
            corrections={"po_number": "PO-1", "status": "hacked", "id": 999},
        ),
        actor="t")
    # only po_number is whitelisted; status/id are ignored
    assert out["corrections_applied"] == {"po_number": "PO-1"}


def test_enqueue_for_review_upserts_in_lakebase():
    captured = []

    class _Cur:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params=None):
            captured.append((sql, params))

    class _Conn:
        def cursor(self):
            return _Cur()

        def commit(self):
            captured.append(("COMMIT", None))

        def close(self):
            pass

    svc = InvoiceService(conn_factory=lambda: _Conn(), sql_fn=None)
    backing = svc.enqueue_for_review(
        file_name="up.pdf", invoice_no="INV-Y", customer="Z",
        extracted_total=99.0, exception_type="total_mismatch")
    assert backing == "lakebase"
    sqls = " ".join(s for s, _ in captured)
    # upsert keyed by file_name (no duplicates on re-upload) + audit log + commit
    assert "ON CONFLICT (file_name) DO UPDATE" in sqls
    assert "invoice_review_queue" in sqls and "app_audit_log" in sqls
    assert ("COMMIT", None) in captured


def test_decided_invoice_leaves_pending_queue():
    """Regression (bug #1): a decided invoice must drop out of the pending queue.

    The review screen fetches ``status='pending'``; approving/rejecting sets the
    row's status server-side, so it must no longer appear in the pending list.
    """
    rows = [
        {"file_name": "invoice_0058.pdf", "invoice_no": "INV-2026-100058",
         "customer": "Kirin Foods Trading", "extracted_total": 2490.4,
         "ground_truth_total": 2490.4, "exception_type": "total_mismatch"},
        {"file_name": "invoice_0001.pdf", "invoice_no": "INV-2024-100001",
         "customer": "Kirin Foods Trading", "extracted_total": 7887.81,
         "ground_truth_total": 7887.81, "exception_type": "missing_po"},
    ]
    InvoiceService(sql_fn=_fake_sql(rows)).list_queue()  # seed
    before = InvoiceService(sql_fn=_fake_sql(rows)).list_queue(status="pending")
    assert {i.file_name for i in before} == {"invoice_0058.pdf", "invoice_0001.pdf"}
    InvoiceService(sql_fn=_fake_sql(rows)).decide(
        "invoice_0058.pdf", InvoiceDecisionRequest(decision="approved"), actor="t")
    after = InvoiceService(sql_fn=_fake_sql(rows)).list_queue(status="pending")
    assert {i.file_name for i in after} == {"invoice_0001.pdf"}


def test_static_sample_queue_matches_real_generated_pdfs():
    """Regression (bug #2a): the no-backend demo queue's fields must match the
    actual PDFs each row previews, or the extracted-fields panel and the rendered
    document disagree. Cross-check _SAMPLE_QUEUE against the seed=42 generator."""
    import importlib.util
    import tempfile
    from pathlib import Path

    import pytest

    # The app vendors a PARTIAL pil_workshop (no datagen), so once that package
    # is imported we can't reach the repo's pil_workshop.datagen by path alone.
    # Load the generator module directly from the repo's src/ under a unique name.
    repo_src = Path(__file__).resolve().parents[3] / "src"
    mod_path = repo_src / "pil_workshop" / "datagen" / "unstructured.py"
    if not mod_path.exists():
        pytest.skip(f"repo datagen not found at {mod_path}")
    spec = importlib.util.spec_from_file_location("_pil_unstructured_for_test", mod_path)
    unstructured = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(unstructured)
    except Exception as exc:  # noqa: BLE001 — reportlab/numpy may be absent
        pytest.skip(f"datagen deps unavailable off-platform: {exc}")

    from backend.services.invoice_service import _SAMPLE_QUEUE

    try:
        # generate_invoice_pdfs lazily imports reportlab AND numpy (via datagen),
        # so guard the CALL too — not just the module load — to skip cleanly when
        # those optional deps aren't installed off-platform.
        gen = unstructured.generate_invoice_pdfs(tempfile.mkdtemp(), n=60, seed=42)
    except Exception as exc:  # noqa: BLE001 — reportlab/numpy may be absent
        pytest.skip(f"datagen deps unavailable off-platform: {exc}")
    gt = {g["file_name"]: g for g in gen}
    for row in _SAMPLE_QUEUE:
        g = gt.get(row["file_name"])
        assert g is not None, f"{row['file_name']} not produced by the generator"
        assert row["invoice_no"] == g["invoice_no"]
        assert row["customer"] == g["customer"]
        assert row["currency"] == g["currency"]
        assert abs(row["extracted_total"] - g["total"]) < 0.01
        # exception_type must match the generator's ground-truth anomaly
        assert row["exception_type"] == g["gt_anomaly"]


# --------------------------------------------------------------------------
# InspectionService
# --------------------------------------------------------------------------
def test_inspection_list_fallback_sample():
    svc = InspectionService(conn_factory=None, sql_fn=None)
    items = svc.list_inspections()
    assert len(items) >= 1
    assert {i.damage for i in items} <= {"none", "minor", "major"}
    assert all(i.image_url.startswith("/api/inspections/") for i in items)


def test_refresh_one_uses_injected_llm():
    class FakeLLM:
        def chat(self, messages, **kwargs):
            assert kwargs["endpoint"] == "databricks-vision-test"
            return '{"damage":"minor","damage_type":"rust","confidence":0.8,' \
                   '"recommended_action":"Flag for manual inspection"}'

    svc = InspectionService()
    result = svc.refresh_one(b"fakebytes", "databricks-vision-test", FakeLLM())
    assert result["damage"] == "minor"
    assert result["damage_type"] == "rust"


def test_refresh_one_salvages_truncated_json():
    """A long reasoning field can truncate the JSON mid-object; the damage label
    must still be recovered rather than returning null fields to the UI."""
    class FakeLLM:
        def chat(self, messages, **kwargs):
            # Valid up to damage/type, then cut off (no closing brace).
            return ('{"reasoning":"This is a real photo with a large caved-in '
                    'panel on the left side that' + "x" * 50 +
                    '","damage":"major","damage_type":"dent","confid')

    svc = InspectionService()
    result = svc.refresh_one(b"fakebytes", "ep", FakeLLM())
    assert result["damage"] == "major"  # salvaged from partial JSON
    assert result["damage_type"] == "dent"


def test_refresh_one_defaults_to_flag_when_unparseable():
    """An unparseable/empty response must never silently clear a container:
    default to a low-confidence 'flag for manual inspection'."""
    class FakeLLM:
        def chat(self, messages, **kwargs):
            return "the model returned prose with no json at all"

    svc = InspectionService()
    result = svc.refresh_one(b"fakebytes", "ep", FakeLLM())
    assert result["damage"] == "none" and result["confidence"] <= 0.4
    assert "flag" in result["recommended_action"].lower()


def test_refresh_one_declares_media_type_matching_the_bytes():
    """Regression: the endpoint rejects a declared media type that disagrees
    with the image content. The data URL must be sniffed from the bytes, not
    hardcoded to image/png (which 400'd on real JPEG uploads)."""
    captured: dict = {}

    class FakeLLM:
        def chat(self, messages, **kwargs):
            captured["url"] = messages[1]["content"][1]["image_url"]["url"]
            return '{"damage":"none","damage_type":"none","confidence":0.9,' \
                   '"recommended_action":"release"}'

    svc = InspectionService()
    # Minimal valid JPEG magic bytes.
    svc.refresh_one(b"\xff\xd8\xff\xe0\x00\x10JFIF", "ep", FakeLLM())
    assert captured["url"].startswith("data:image/jpeg;base64,")

    svc.refresh_one(b"\x89PNG\r\n\x1a\n\x00\x00", "ep", FakeLLM())
    assert captured["url"].startswith("data:image/png;base64,")


def test_save_and_analyze_uploads_and_returns_metrics():
    class _FakeFiles:
        def __init__(self):
            self.saved = {}

        def upload(self, path, buf, overwrite=False):  # noqa: ANN001
            self.saved[path] = buf.read()

    class _FakeWC:
        def __init__(self):
            self.files = _FakeFiles()

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return '{"damage":"major","damage_type":"dent","confidence":0.91,' \
                   '"recommended_action":"Remove from service"}'

    writes: list = []
    wc = _FakeWC()
    svc = InspectionService(
        workspace_client=wc, write_fn=lambda sql, params: writes.append((sql, params))
    )
    r = svc.save_and_analyze("../x/cont.png", b"%PNGbytes", "vision-ep", FakeLLM())
    assert r["file_name"] == "cont.png"  # basename only
    assert r["damage"] == "major" and r["confidence"] == 0.91
    assert any(p.endswith("/container_images/cont.png") for p in wc.files.saved)
    m = r["metrics"]
    assert m["duration_ms"] >= m["analyze_ms"] and m["est_total_tokens"] > 0
    assert m["model_endpoint"] == "vision-ep"
    # Persisted one row to the container Delta sink (parameterized INSERT).
    assert len(writes) == 1
    sql, params = writes[0]
    assert "container_inspections_app" in sql and sql.strip().upper().startswith("INSERT")
    assert params["file_name"] == "cont.png" and params["damage"] == "major"
    assert params["model_endpoint"] == "vision-ep"


def test_save_and_analyze_survives_persist_failure():
    """A Delta write error must never fail the analysis response."""
    class _FakeWC:
        class files:  # noqa: N801
            @staticmethod
            def upload(path, buf, overwrite=False):  # noqa: ANN001
                pass

    class FakeLLM:
        def chat(self, messages, **kwargs):
            return '{"damage":"none","damage_type":"none","confidence":0.9,' \
                   '"recommended_action":"release"}'

    def _boom(sql, params):
        raise RuntimeError("no MODIFY grant")

    svc = InspectionService(workspace_client=_FakeWC(), write_fn=_boom)
    r = svc.save_and_analyze("c.png", b"%PNGbytes", "ep", FakeLLM())
    assert r["damage"] == "none"  # analysis still returned despite write failure


def test_list_inspections_merges_uploads_over_batch_and_dedups():
    """Live uploads appear in the gallery, win dedup over the batch row, and a
    missing sink never blanks the batch set."""
    scored = [
        {"file_name": "container_0001.png", "container_no": "PILU1", "damage": "none",
         "damage_type": "none", "confidence": 0.9, "recommended_action": "release",
         "gt_damage": "none", "is_correct": 1},
        {"file_name": "shared.png", "container_no": "PILU2", "damage": "minor",
         "damage_type": "rust", "confidence": 0.7, "recommended_action": "flag",
         "gt_damage": "minor", "is_correct": 1},
    ]
    uploads = [
        {"file_name": "real_crushed.jpg", "damage": "major", "damage_type": "dent",
         "confidence": 0.95, "recommended_action": "remove from service"},
        {"file_name": "shared.png", "damage": "major", "damage_type": "dent",
         "confidence": 0.99, "recommended_action": "remove from service"},
    ]

    def _sql(sql):
        # The uploads read selects from the container sink; the batch read from
        # the scored table. Route by table name in the SQL.
        return uploads if "container_inspections_app" in sql else scored

    svc = InspectionService(sql_fn=_sql)
    items = {it.file_name: it for it in svc.list_inspections()}
    # The uploaded-only image is present.
    assert "real_crushed.jpg" in items and items["real_crushed.jpg"].damage == "major"
    # Batch-only image is preserved.
    assert "container_0001.png" in items
    # Shared file appears once and the upload wins (0.99, not the batch 0.7).
    all_shared = [it for it in svc.list_inspections() if it.file_name == "shared.png"]
    assert len(all_shared) == 1 and all_shared[0].confidence == 0.99


def test_accuracy_summary_computes_confusions():
    def _sql(sql):
        return [
            {"pred_damage": "none", "gt_damage": "none", "n": 20},
            {"pred_damage": "minor", "gt_damage": "minor", "n": 8},
            {"pred_damage": "major", "gt_damage": "minor", "n": 5},  # wrong
            {"pred_damage": "none", "gt_damage": "minor", "n": 3},   # wrong
        ]

    svc = InspectionService(sql_fn=_sql)
    a = svc.accuracy_summary()
    assert a["scored"] == 36 and a["correct"] == 28
    assert a["accuracy_pct"] == round(100 * 28 / 36, 1)
    assert a["confusions"] == {"major→minor": 5, "none→minor": 3}


def test_accuracy_summary_empty_without_sql():
    a = InspectionService(sql_fn=None).accuracy_summary()
    assert a["scored"] == 0 and a["accuracy_pct"] is None


def test_create_work_order_demo_mode_persists_and_counts():
    svc = InspectionService(conn_factory=None)
    out = svc.create_work_order(
        WorkOrderRequest(file_name="c.png", container_no="PILU1", damage="major"),
        actor="tester",
    )
    assert out["file_name"] == "c.png"
    assert out["status"] == "open"
    assert out["work_order_id"] == 1
    # The open count is now observable via analytics (Home KPI).
    analytics = AnalyticsService(inspection_service=InspectionService(conn_factory=None))
    assert analytics.kpi_summary().open_work_orders == 1


def test_list_inspections_guards_unexpected_damage_label():
    bad = [{"file_name": "x.png", "container_no": "P1", "damage": "catastrophic",
            "damage_type": "dent", "confidence": 0.5, "recommended_action": "x"}]
    items = InspectionService(sql_fn=_fake_sql(bad)).list_inspections()
    # Unexpected label is coerced to None rather than raising a 500.
    assert items[0].damage is None


# --------------------------------------------------------------------------
# AnalyticsService
# --------------------------------------------------------------------------
def test_kpi_summary_demo_defaults():
    inv = InvoiceService(conn_factory=None, sql_fn=None)
    svc = AnalyticsService(sql_fn=None, invoice_service=inv)
    summary = svc.kpi_summary()
    # Falls back to demo reliability/utilization when UC not reachable.
    assert 60 <= summary.schedule_reliability_pct <= 85
    assert 70 <= summary.vessel_utilization_pct <= 95
    assert summary.pending_reviews >= 1


def test_usage_summary_from_sql():
    daily = [
        {"usage_date": "2026-08-01", "total_tokens": 1000, "request_count": 5,
         "est_cost_usd": 0.01},
        {"usage_date": "2026-08-02", "total_tokens": 2000, "request_count": 9,
         "est_cost_usd": 0.02},
    ]
    by_ep = [
        {"endpoint": "databricks-claude-sonnet-4-5", "total_tokens": 2500,
         "request_count": 11, "est_cost_usd": 0.0125},
        {"endpoint": "databricks-claude-vision", "total_tokens": 500,
         "request_count": 3, "est_cost_usd": 0.0025},
    ]

    def _routed(sql):
        return list(by_ep) if "v_ai_usage_by_endpoint" in sql else list(daily)

    svc = AnalyticsService(sql_fn=_routed)
    usage = svc.usage_summary()
    # today = last day in the series
    assert usage.today_tokens == 2000
    assert len(usage.series) == 2
    # all-time = sum across the whole daily history
    assert usage.all_time_tokens == 3000
    assert usage.all_time_requests == 14
    assert round(usage.all_time_cost_usd, 2) == 0.03
    # per-endpoint breakdown, largest-first as returned
    assert [e.endpoint for e in usage.by_endpoint] == [
        "databricks-claude-sonnet-4-5", "databricks-claude-vision"]
    assert usage.by_endpoint[0].total_tokens == 2500


def test_usage_summary_all_time_totals_span_full_history():
    """Regression: all-time totals must sum the ENTIRE daily history, not just
    the last 30 points kept for the trend chart."""
    daily = [
        {"usage_date": f"2026-06-{d:02d}", "total_tokens": 100, "request_count": 1,
         "est_cost_usd": 0.001}
        for d in range(1, 31)  # 30 days
    ] + [
        {"usage_date": f"2026-07-{d:02d}", "total_tokens": 100, "request_count": 1,
         "est_cost_usd": 0.001}
        for d in range(1, 11)  # +10 more days = 40 total
    ]

    def _routed(sql):
        return [] if "v_ai_usage_by_endpoint" in sql else list(daily)

    usage = AnalyticsService(sql_fn=_routed).usage_summary()
    assert len(usage.series) == 30            # chart capped to last 30
    assert usage.all_time_requests == 40      # totals span all 40 days
    assert usage.all_time_tokens == 4000
