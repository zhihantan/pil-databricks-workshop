"""API-level smoke tests using FastAPI's TestClient with dependency overrides."""

from __future__ import annotations

from fastapi.testclient import TestClient

from backend.deps import (
    get_analytics_service,
    get_inspection_service,
    get_invoice_service,
)
from backend.main import app
from backend.services.analytics_service import AnalyticsService
from backend.services.inspection_service import InspectionService
from backend.services.invoice_service import InvoiceService

# Override services with backend-less (in-memory/sample) instances.
app.dependency_overrides[get_invoice_service] = lambda: InvoiceService()
app.dependency_overrides[get_inspection_service] = lambda: InspectionService()
app.dependency_overrides[get_analytics_service] = lambda: AnalyticsService(
    invoice_service=InvoiceService()
)

client = TestClient(app)


def test_health():
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body["catalog"]
    assert "lakebase" in body


def test_health_ok_when_warehouse_reachable_even_without_lakebase(monkeypatch):
    """UC-only (no Lakebase) is a supported mode: health keys on warehouse
    reachability, not Lakebase. Warehouse up + Lakebase down → still 'ok'."""
    import backend.routers.health as health

    monkeypatch.setattr(health, "sql_connection_target", lambda: ("host", "/path"))
    monkeypatch.setattr(health, "lakebase_available", lambda: False)
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["lakebase"] is False  # reported as informational sub-status


def test_health_degraded_when_no_warehouse(monkeypatch):
    """No SQL warehouse means UC reads + extraction can't work → degraded,
    regardless of Lakebase."""
    import backend.routers.health as health

    monkeypatch.setattr(health, "sql_connection_target", lambda: None)
    monkeypatch.setattr(health, "lakebase_available", lambda: True)
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"


def test_safe_catalog_accepts_valid_identifier(monkeypatch):
    from backend.core.config import _safe_catalog

    monkeypatch.setenv("PIL_CATALOG", "my_catalog2")
    assert _safe_catalog() == "my_catalog2"


def test_safe_catalog_defaults_when_unset(monkeypatch):
    from backend.core.config import _safe_catalog

    monkeypatch.delenv("PIL_CATALOG", raising=False)
    assert _safe_catalog() == "pil_workshop"


def test_safe_catalog_rejects_injection_and_falls_back(monkeypatch):
    """A malformed PIL_CATALOG (dot/backtick/hyphen) is interpolated into
    silver/gold SQL identifiers, so it must fall back to the safe default rather
    than break queries or inject."""
    from backend.core.config import _safe_catalog

    for bad in ("a.b", "cat`; DROP", "has-hyphen", "1leading_digit", ""):
        monkeypatch.setenv("PIL_CATALOG", bad)
        assert _safe_catalog() == "pil_workshop"


def test_list_invoices():
    r = client.get("/api/invoices")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


def test_decision_roundtrip():
    items = client.get("/api/invoices").json()
    fn = items[0]["file_name"]
    r = client.post(f"/api/invoices/{fn}/decision", json={"decision": "approved"})
    assert r.status_code == 200
    assert r.json()["decision"] == "approved"


def test_list_inspections():
    r = client.get("/api/inspections")
    assert r.status_code == 200
    assert len(r.json()) >= 1


def test_work_order():
    r = client.post("/api/inspections/work-order",
                    json={"file_name": "container_0003.png", "damage": "major"})
    assert r.status_code == 200
    assert r.json()["file_name"] == "container_0003.png"


def test_kpis_and_usage():
    assert client.get("/api/kpis").status_code == 200
    assert client.get("/api/usage").status_code == 200
