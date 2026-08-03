import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { Decision, InvoiceQueueItem } from "../api/types";
import { useToast } from "../components/Toast";
import {
  currency,
  EmptyState,
  ExceptionBadge,
  PageHeader,
  Skeleton,
} from "../components/ui";

export function InvoiceReview() {
  const qc = useQueryClient();
  const toast = useToast();
  const [selected, setSelected] = useState<InvoiceQueueItem | null>(null);
  const [adjusted, setAdjusted] = useState<string>("");

  const queue = useQuery({ queryKey: ["invoices"], queryFn: () => api.listInvoices() });

  const decide = useMutation({
    mutationFn: ({ fileName, decision }: { fileName: string; decision: Decision }) =>
      api.decideInvoice(fileName, {
        decision,
        // Parse explicitly so an adjustment to 0 isn't dropped by `|| null`.
        adjusted_total:
          decision === "adjusted" && adjusted.trim() !== ""
            ? Number(adjusted)
            : null,
      }),
    onSuccess: (_data, vars) => {
      toast.push(`Invoice ${vars.decision}`, "success");
      qc.invalidateQueries({ queryKey: ["invoices"] });
      qc.invalidateQueries({ queryKey: ["kpis"] });
      setSelected(null);
    },
    onError: (e: Error) => toast.push(e.message, "error"),
  });

  const items = queue.data ?? [];
  const active = selected ?? items[0] ?? null;
  const mismatch =
    active &&
    active.ground_truth_total != null &&
    active.extracted_total != null &&
    Math.abs(active.ground_truth_total - active.extracted_total) > 1;

  return (
    <>
      <PageHeader
        title="Invoice Review"
        subtitle="Human-in-the-loop review of extracted freight invoices. Decisions flow to Lakebase and back to analytics."
      />

      {queue.isLoading ? (
        <Skeleton height={400} />
      ) : items.length === 0 ? (
        <EmptyState
          emoji="🧾"
          text="No invoices in the review queue. Run notebooks 07–09."
        />
      ) : (
        <div className="split">
          {/* Left: PDF preview */}
          <div className="card">
            <div className="preview-head">
              <h2 className="section-title" style={{ margin: 0 }}>
                Document preview
              </h2>
              {active?.pdf_preview_url && (
                <a
                  className="btn btn-ghost btn-sm"
                  href={active.pdf_preview_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  ↗ Open PDF
                </a>
              )}
            </div>
            {active?.pdf_preview_url ? (
              <object
                key={active.file_name}
                className="pdf-frame"
                data={active.pdf_preview_url}
                type="application/pdf"
              >
                <div className="empty">
                  <div className="empty-emoji">📄</div>
                  <p>
                    Your browser can't render PDFs inline —{" "}
                    <a href={active.pdf_preview_url} target="_blank" rel="noreferrer">
                      open {active.file_name}
                    </a>{" "}
                    in a new tab.
                  </p>
                </div>
              </object>
            ) : (
              <EmptyState emoji="📄" text="Select an invoice to preview." />
            )}
          </div>

          {/* Right: extracted fields + queue */}
          <div>
            <div className="card" style={{ marginBottom: 16 }}>
              <h2 className="section-title">
                Extracted fields{" "}
                {active && <ExceptionBadge type={active.exception_type} />}
              </h2>
              {active && (
                <>
                  <Field label="Invoice No" value={active.invoice_no ?? "—"} />
                  <Field label="Customer" value={active.customer ?? "—"} />
                  <div className="field-row">
                    <span className="field-label">Extracted total</span>
                    <span className={`field-value${mismatch ? " mismatch" : ""}`}>
                      {currency(active.extracted_total)}
                    </span>
                  </div>
                  {mismatch && (
                    <div className="field-row">
                      <span className="field-label">Expected total</span>
                      <span className="field-value">
                        {currency(active.ground_truth_total)}
                      </span>
                    </div>
                  )}
                  <div className="field-row">
                    <span className="field-label">Adjust total</span>
                    <span className="field-value">
                      <input
                        type="number"
                        placeholder="optional"
                        value={adjusted}
                        onChange={(e) => setAdjusted(e.target.value)}
                      />
                    </span>
                  </div>
                  <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
                    <button
                      className="btn btn-approve"
                      disabled={decide.isPending}
                      onClick={() =>
                        decide.mutate({
                          fileName: active.file_name,
                          decision: "approved",
                        })
                      }
                    >
                      Approve
                    </button>
                    <button
                      className="btn btn-ghost"
                      disabled={decide.isPending || !adjusted}
                      onClick={() =>
                        decide.mutate({
                          fileName: active.file_name,
                          decision: "adjusted",
                        })
                      }
                    >
                      Save adjustment
                    </button>
                    <button
                      className="btn btn-reject"
                      disabled={decide.isPending}
                      onClick={() =>
                        decide.mutate({
                          fileName: active.file_name,
                          decision: "rejected",
                        })
                      }
                    >
                      Reject
                    </button>
                  </div>
                </>
              )}
            </div>

            <div className="card">
              <h2 className="section-title">Review queue ({items.length})</h2>
              <table className="data">
                <thead>
                  <tr>
                    <th>Invoice</th>
                    <th>Customer</th>
                    <th>Exception</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((it) => (
                    <tr
                      key={it.file_name}
                      className={`row-selectable${
                        active?.file_name === it.file_name ? " row-selected" : ""
                      }`}
                      onClick={() => {
                        setSelected(it);
                        setAdjusted("");
                      }}
                    >
                      <td>{it.invoice_no ?? it.file_name}</td>
                      <td>{it.customer ?? "—"}</td>
                      <td>
                        <ExceptionBadge type={it.exception_type} />
                      </td>
                      <td>{it.status}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="field-row">
      <span className="field-label">{label}</span>
      <span className="field-value">{value}</span>
    </div>
  );
}
