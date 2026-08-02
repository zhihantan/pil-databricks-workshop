"""Pydantic v2 schemas for the PIL app API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class InvoiceLineItem(BaseModel):
    description: str | None = None
    amount: float | None = None


class ExtractedInvoice(BaseModel):
    """Structured output of the upload → parse → extract flow."""

    file_name: str
    volume_path: str
    invoice_no: str | None = None
    customer: str | None = None
    po_number: str | None = None
    currency: str | None = None
    date: str | None = None
    payment_terms: str | None = None
    subtotal: float | None = None
    tax: float | None = None
    total: float | None = None
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    exception_type: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"] = "ok"
    lakebase: bool = Field(description="Whether Lakebase is reachable")
    catalog: str
    version: str


class InvoiceQueueItem(BaseModel):
    id: int
    file_name: str
    invoice_no: str | None = None
    customer: str | None = None
    extracted_total: float | None = None
    ground_truth_total: float | None = None
    exception_type: str | None = None
    status: str = "pending"
    pdf_preview_url: str | None = None


class InvoiceDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "adjusted"]
    reason: str | None = None
    adjusted_total: float | None = None


class InspectionItem(BaseModel):
    file_name: str
    container_no: str | None = None
    damage: Literal["none", "minor", "major"] | None = None
    damage_type: str | None = None
    confidence: float | None = None
    recommended_action: str | None = None
    image_url: str | None = None


class WorkOrderRequest(BaseModel):
    file_name: str
    container_no: str | None = None
    damage: str | None = None
    damage_type: str | None = None
    action: str = "Flag for manual inspection"


class KpiSummary(BaseModel):
    pending_reviews: int
    open_work_orders: int
    invoices_processed: int
    containers_inspected: int
    inspection_accuracy_pct: float | None = None
    schedule_reliability_pct: float | None = None
    vessel_utilization_pct: float | None = None


class UsageDailyPoint(BaseModel):
    usage_date: str
    total_tokens: int = 0
    request_count: int = 0
    est_cost_usd: float = 0.0


class UsageSummary(BaseModel):
    today_tokens: int = 0
    today_requests: int = 0
    today_cost_usd: float = 0.0
    series: list[UsageDailyPoint] = Field(default_factory=list)


class ActivityItem(BaseModel):
    actor: str
    action: str
    entity: str | None = None
    created_at: datetime
