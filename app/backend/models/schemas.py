"""Pydantic v2 schemas for the PIL app API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class InvoiceLineItem(BaseModel):
    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    amount: float | None = None


class ExtractionMetrics(BaseModel):
    """Run metrics for one extraction (shown in the app while/after loading)."""

    duration_ms: int = 0
    save_ms: int = 0
    extract_ms: int = 0
    doc_chars: int = 0
    est_input_tokens: int = 0
    est_output_tokens: int = 0
    est_total_tokens: int = 0
    est_cost_usd: float = 0.0
    model_endpoint: str | None = None
    field_count: int = 0
    line_item_count: int = 0


class ExtractedInvoice(BaseModel):
    """Structured output of the upload → parse → extract flow (rich ~22 fields)."""

    file_name: str
    volume_path: str
    # header
    invoice_no: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    purchase_order: str | None = None
    # parties
    vendor_name: str | None = None
    vendor_tax_id: str | None = None
    vendor_address: str | None = None
    customer_name: str | None = None
    customer_address: str | None = None
    # freight / shipping
    currency: str | None = None
    incoterms: str | None = None
    bill_of_lading: str | None = None
    vessel_name: str | None = None
    container_numbers: list[str] = Field(default_factory=list)
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    # terms
    payment_terms: str | None = None
    bank_details: str | None = None
    notes: str | None = None
    # money
    subtotal: float | None = None
    discount: float | None = None
    shipping: float | None = None
    tax: float | None = None
    tax_rate: str | None = None
    total: float | None = None
    amount_paid: float | None = None
    balance_due: float | None = None
    line_items: list[InvoiceLineItem] = Field(default_factory=list)
    # derived / meta
    exception_type: str | None = None
    metrics: ExtractionMetrics | None = None
    saved_table: str | None = None  # Delta table the row was persisted to
    queued_for_review: bool = False  # flagged → added to the Lakebase review queue


class ProcessedInvoice(BaseModel):
    """One row for the 'processed invoices' list (read from the Delta sink)."""

    source_file: str
    invoice_no: str | None = None
    customer_name: str | None = None
    currency: str | None = None
    total: float | None = None
    exception_type: str | None = None
    extracted_at: str | None = None


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
    po_number: str | None = None
    currency: str | None = None
    extracted_total: float | None = None
    ground_truth_total: float | None = None
    exception_type: str | None = None
    status: str = "pending"
    pdf_preview_url: str | None = None


class InvoiceDecisionRequest(BaseModel):
    decision: Literal["approved", "rejected", "adjusted"]
    reason: str | None = None
    adjusted_total: float | None = None
    # Reviewer corrections keyed by field name (po_number, currency, invoice_no,
    # customer, total). Applied to the queue row so a flagged gap is resolved.
    corrections: dict[str, str | float | None] = Field(default_factory=dict)


class InspectionItem(BaseModel):
    file_name: str
    container_no: str | None = None
    damage: Literal["none", "minor", "major"] | None = None
    damage_type: str | None = None
    confidence: float | None = None
    recommended_action: str | None = None
    image_url: str | None = None
    # ground-truth comparison (from container_inspections_scored), when available
    gt_damage: str | None = None
    is_correct: bool | None = None


class InspectionAccuracy(BaseModel):
    """Accuracy summary of the vision agent vs labelled ground truth."""

    scored: int = 0
    correct: int = 0
    accuracy_pct: float | None = None
    # simple off-diagonal confusion counts: "predicted→actual" -> n
    confusions: dict[str, int] = Field(default_factory=dict)


class VisionMetrics(BaseModel):
    """Run metrics for a single container-image analysis."""

    duration_ms: int = 0
    save_ms: int = 0
    analyze_ms: int = 0
    est_input_tokens: int = 0
    est_output_tokens: int = 0
    est_total_tokens: int = 0
    est_cost_usd: float = 0.0
    model_endpoint: str | None = None


class ContainerAnalysis(BaseModel):
    """Result of uploading + analyzing one container image."""

    file_name: str
    image_url: str | None = None
    damage: str | None = None
    damage_type: str | None = None
    confidence: float | None = None
    recommended_action: str | None = None
    metrics: VisionMetrics | None = None


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


class UsageEndpointPoint(BaseModel):
    """All-time usage for one governed FMAPI endpoint the agents call."""

    endpoint: str
    total_tokens: int = 0
    request_count: int = 0
    est_cost_usd: float = 0.0


class UsageSummary(BaseModel):
    # Today (last day in the series) — kept for the at-a-glance tiles.
    today_tokens: int = 0
    today_requests: int = 0
    today_cost_usd: float = 0.0
    # All-time totals for this project's agents (sum across the usage views,
    # which are now unbounded — see notebook 01b).
    all_time_tokens: int = 0
    all_time_requests: int = 0
    all_time_cost_usd: float = 0.0
    # Per-endpoint all-time breakdown (text vs vision), largest first.
    by_endpoint: list[UsageEndpointPoint] = Field(default_factory=list)
    series: list[UsageDailyPoint] = Field(default_factory=list)


class ActivityItem(BaseModel):
    actor: str
    action: str
    entity: str | None = None
    created_at: datetime
