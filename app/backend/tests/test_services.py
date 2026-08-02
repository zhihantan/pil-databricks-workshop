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
    rows = [
        {"usage_date": "2026-08-01", "total_tokens": 1000, "request_count": 5,
         "est_cost_usd": 0.01},
        {"usage_date": "2026-08-02", "total_tokens": 2000, "request_count": 9,
         "est_cost_usd": 0.02},
    ]
    svc = AnalyticsService(sql_fn=_fake_sql(rows))
    usage = svc.usage_summary()
    assert usage.today_tokens in (1000, 2000)
    assert len(usage.series) == 2
