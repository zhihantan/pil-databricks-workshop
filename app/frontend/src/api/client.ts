// Thin fetch client for the FastAPI backend. Same-origin on Databricks Apps;
// the Vite dev server proxies /api to localhost:8000.

import type {
  HealthResponse,
  InspectionItem,
  InvoiceDecisionRequest,
  InvoiceQueueItem,
  KpiSummary,
  UsageSummary,
  WorkOrderRequest,
} from "./types";

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body.detail ?? detail;
    } catch {
      /* non-JSON error */
    }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<HealthResponse>("/api/health"),
  kpis: () => req<KpiSummary>("/api/kpis"),
  usage: () => req<UsageSummary>("/api/usage"),

  listInvoices: (status?: string) =>
    req<InvoiceQueueItem[]>(`/api/invoices${status ? `?status=${status}` : ""}`),
  decideInvoice: (fileName: string, body: InvoiceDecisionRequest) =>
    req<{ decision: string }>(`/api/invoices/${encodeURIComponent(fileName)}/decision`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  listInspections: () => req<InspectionItem[]>("/api/inspections"),
  refreshInspection: (fileName: string) =>
    req<{ result: Record<string, unknown> }>(
      `/api/inspections/${encodeURIComponent(fileName)}/refresh`,
      { method: "POST" },
    ),
  createWorkOrder: (body: WorkOrderRequest) =>
    req<{ file_name: string; status: string }>("/api/inspections/work-order", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
