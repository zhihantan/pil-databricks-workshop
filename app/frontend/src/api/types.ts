// API response types mirroring backend/models/schemas.py.

export interface InvoiceLineItem {
  description: string | null;
  quantity: number | null;
  unit_price: number | null;
  amount: number | null;
}

export interface ExtractionMetrics {
  duration_ms: number;
  save_ms: number;
  extract_ms: number;
  doc_chars: number;
  est_input_tokens: number;
  est_output_tokens: number;
  est_total_tokens: number;
  est_cost_usd: number;
  model_endpoint: string | null;
  field_count: number;
  line_item_count: number;
}

export interface ExtractedInvoice {
  file_name: string;
  volume_path: string;
  // header
  invoice_no: string | null;
  invoice_date: string | null;
  due_date: string | null;
  purchase_order: string | null;
  // parties
  vendor_name: string | null;
  vendor_tax_id: string | null;
  vendor_address: string | null;
  customer_name: string | null;
  customer_address: string | null;
  // freight / shipping
  currency: string | null;
  incoterms: string | null;
  bill_of_lading: string | null;
  vessel_name: string | null;
  container_numbers: string[];
  port_of_loading: string | null;
  port_of_discharge: string | null;
  // terms
  payment_terms: string | null;
  bank_details: string | null;
  notes: string | null;
  // money
  subtotal: number | null;
  discount: number | null;
  shipping: number | null;
  tax: number | null;
  tax_rate: string | null;
  total: number | null;
  amount_paid: number | null;
  balance_due: number | null;
  line_items: InvoiceLineItem[];
  // derived / meta
  exception_type: string | null;
  metrics: ExtractionMetrics | null;
  saved_table: string | null;
  queued_for_review: boolean;
}

export interface HealthResponse {
  status: "ok" | "degraded";
  lakebase: boolean;
  catalog: string;
  version: string;
}

export interface InvoiceQueueItem {
  id: number;
  file_name: string;
  invoice_no: string | null;
  customer: string | null;
  extracted_total: number | null;
  ground_truth_total: number | null;
  exception_type: string | null;
  status: string;
  pdf_preview_url: string | null;
}

export type Decision = "approved" | "rejected" | "adjusted";

export interface InvoiceDecisionRequest {
  decision: Decision;
  reason?: string | null;
  adjusted_total?: number | null;
}

export type Damage = "none" | "minor" | "major";

export interface InspectionItem {
  file_name: string;
  container_no: string | null;
  damage: Damage | null;
  damage_type: string | null;
  confidence: number | null;
  recommended_action: string | null;
  image_url: string | null;
}

export interface WorkOrderRequest {
  file_name: string;
  container_no?: string | null;
  damage?: string | null;
  damage_type?: string | null;
  action?: string;
}

export interface KpiSummary {
  pending_reviews: number;
  open_work_orders: number;
  invoices_processed: number;
  containers_inspected: number;
  inspection_accuracy_pct: number | null;
  schedule_reliability_pct: number | null;
  vessel_utilization_pct: number | null;
}

export interface UsageDailyPoint {
  usage_date: string;
  total_tokens: number;
  request_count: number;
  est_cost_usd: number;
}

export interface UsageSummary {
  today_tokens: number;
  today_requests: number;
  today_cost_usd: number;
  series: UsageDailyPoint[];
}
