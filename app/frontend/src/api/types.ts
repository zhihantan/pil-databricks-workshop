// API response types mirroring backend/models/schemas.py.

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
