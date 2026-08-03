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
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  // Reviewer corrections for the active invoice, keyed by field name.
  const [corr, setCorr] = useState<Record<string, string>>({});

  const queue = useQuery({ queryKey: ["invoices"], queryFn: () => api.listInvoices() });

  const decide = useMutation({
    mutationFn: ({
      fileName,
      decision,
      corrections,
    }: {
      fileName: string;
      decision: Decision;
      corrections?: Record<string, string | number | null>;
    }) => api.decideInvoice(fileName, { decision, corrections }),
    onSuccess: (_data, vars) => {
      toast.push(
        vars.decision === "rejected" ? "Invoice rejected" : "Invoice approved",
        "success",
      );
      qc.invalidateQueries({ queryKey: ["invoices"] });
      qc.invalidateQueries({ queryKey: ["kpis"] });
      setSelectedFile(null);
      setCorr({});
    },
    onError: (e: Error) => toast.push(e.message, "error"),
  });

  const items = queue.data ?? [];
  const active =
    (selectedFile && items.find((i) => i.file_name === selectedFile)) ||
    items[0] ||
    null;

  // Which fields does this exception ask the reviewer to supply/verify?
  const fieldsFor = (ex: string | null): CorrectionField[] => {
    if (ex === "missing_po") return ["po_number"];
    if (ex === "total_mismatch") return ["total"];
    if (ex === "missing_fields")
      return (["invoice_no", "customer", "currency", "total"] as CorrectionField[]).filter(
        (f) => isBlank(active, f),
      );
    return [];
  };
  const needed = active ? fieldsFor(active.exception_type) : [];

  const submit = (decision: Decision) => {
    if (!active) return;
    const corrections: Record<string, string | number | null> = {};
    for (const [k, v] of Object.entries(corr)) {
      if (v.trim() === "") continue;
      corrections[k] = k === "total" ? Number(v) : v.trim();
    }
    decide.mutate({ fileName: active.file_name, decision, corrections });
  };

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
          <div className="card pdf-card">
            {active?.pdf_preview_url ? (
              <div className="pdf-viewer">
                <div className="pdf-toolbar">
                  <span className="pdf-toolbar-file" title={active.file_name}>
                    <span className="pdf-toolbar-ico">📄</span>
                    {active.file_name}
                  </span>
                  <span className="pdf-toolbar-actions">
                    <ExceptionBadge type={active.exception_type} />
                    <a
                      className="btn btn-ghost btn-sm"
                      href={active.pdf_preview_url}
                      target="_blank"
                      rel="noreferrer"
                    >
                      ↗ Open
                    </a>
                  </span>
                </div>
                <div className="pdf-stage">
                  <object
                    key={active.file_name}
                    className="pdf-frame"
                    data={`${active.pdf_preview_url}#toolbar=1&view=FitH`}
                    type="application/pdf"
                  >
                    <div className="pdf-fallback">
                      <div className="empty-emoji">📄</div>
                      <p className="muted">
                        Your browser can't render PDFs inline.
                      </p>
                      <a
                        className="btn btn-primary btn-sm"
                        href={active.pdf_preview_url}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Open {active.file_name}
                      </a>
                    </div>
                  </object>
                </div>
              </div>
            ) : (
              <div className="pdf-empty">
                <EmptyState
                  emoji="📄"
                  text="Select an invoice from the queue to preview its document."
                />
              </div>
            )}
          </div>

          {/* Right: extracted fields + correction + queue */}
          <div>
            <div className="card" style={{ marginBottom: 16 }}>
              <h2 className="section-title">
                Extracted fields{" "}
                {active && <ExceptionBadge type={active.exception_type} />}
              </h2>
              {active && (
                <>
                  {active.exception_type && (
                    <div className="review-callout">
                      {flagGuidance(active.exception_type, needed)}
                    </div>
                  )}

                  <Field label="Invoice No" value={active.invoice_no ?? "—"} />
                  <Field label="Customer" value={active.customer ?? "—"} />
                  <Field label="PO Number" value={active.po_number ?? "— (missing)"} />
                  <div className="field-row">
                    <span className="field-label">Extracted total</span>
                    <span className="field-value">
                      {currency(active.extracted_total)} {active.currency ?? ""}
                    </span>
                  </div>

                  {/* Context-aware correction inputs — only what the flag needs. */}
                  {needed.length > 0 && (
                    <div className="correct-box">
                      <div className="correct-title">Correct &amp; resolve</div>
                      {needed.map((f) => (
                        <div key={f} className="field-row">
                          <span className="field-label">{FIELD_LABEL[f]}</span>
                          <span className="field-value">
                            <input
                              type={f === "total" ? "number" : "text"}
                              placeholder={placeholderFor(f)}
                              value={corr[f] ?? ""}
                              onChange={(e) =>
                                setCorr((c) => ({ ...c, [f]: e.target.value }))
                              }
                            />
                          </span>
                        </div>
                      ))}
                    </div>
                  )}

                  <div style={{ display: "flex", gap: 10, marginTop: 16 }}>
                    <button
                      className="btn btn-approve"
                      disabled={decide.isPending}
                      onClick={() => submit("approved")}
                    >
                      {hasEdits(corr) ? "Save & approve" : "Approve"}
                    </button>
                    <button
                      className="btn btn-reject"
                      disabled={decide.isPending}
                      onClick={() => submit("rejected")}
                    >
                      Reject
                    </button>
                  </div>
                  <p className="muted" style={{ fontSize: 12, marginTop: 10 }}>
                    Approve posts the invoice (with your corrections) and clears it from
                    the queue. Reject sends it back — nothing is posted.
                  </p>
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
                        setSelectedFile(it.file_name);
                        setCorr({});
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

type CorrectionField = "po_number" | "currency" | "invoice_no" | "customer" | "total";

const FIELD_LABEL: Record<CorrectionField, string> = {
  po_number: "PO Number",
  currency: "Currency",
  invoice_no: "Invoice No",
  customer: "Customer",
  total: "Corrected total",
};

function isBlank(item: InvoiceQueueItem | null, f: CorrectionField): boolean {
  if (!item) return false;
  if (f === "total") return item.extracted_total == null;
  return !item[f];
}

function placeholderFor(f: CorrectionField): string {
  if (f === "total") return "enter correct total";
  if (f === "currency") return "e.g. USD";
  return `enter ${FIELD_LABEL[f].toLowerCase()}`;
}

function hasEdits(corr: Record<string, string>): boolean {
  return Object.values(corr).some((v) => v.trim() !== "");
}

function flagGuidance(ex: string, needed: CorrectionField[]): string {
  if (ex === "missing_po")
    return "⚠ No PO number was extracted. Check the document — add the PO below if it applies, or approve as-is when the invoice legitimately has none.";
  if (ex === "total_mismatch")
    return "⚠ The total doesn't reconcile with subtotal, tax and adjustments. Verify against the document and enter the correct total below.";
  if (ex === "missing_fields")
    return `⚠ Key field(s) missing: ${needed
      .map((f) => FIELD_LABEL[f])
      .join(", ")}. Fill them in from the document, then approve.`;
  return "⚠ Flagged for review — verify against the document before approving.";
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="field-row">
      <span className="field-label">{label}</span>
      <span className="field-value">{value}</span>
    </div>
  );
}
