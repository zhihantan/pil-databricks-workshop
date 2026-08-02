import { useMutation } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { api } from "../api/client";
import type { ExtractedInvoice } from "../api/types";
import { useToast } from "../components/Toast";
import {
  currency,
  EmptyState,
  ExceptionBadge,
  PageHeader,
  Skeleton,
} from "../components/ui";

export function UploadExtract() {
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [fileName, setFileName] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [result, setResult] = useState<ExtractedInvoice | null>(null);

  const upload = useMutation({
    mutationFn: (f: File) => api.uploadInvoice(f),
    onSuccess: (data) => {
      setResult(data);
      toast.push(
        data.exception_type
          ? `Extracted — flagged: ${data.exception_type}`
          : "Extracted successfully",
        data.exception_type ? "info" : "success",
      );
    },
    onError: (e: Error) => toast.push(e.message, "error"),
  });

  const onPick = (files: FileList | null) => {
    const f = files?.[0];
    if (!f) return;
    setFileName(f.name);
    setResult(null);
    upload.mutate(f);
  };

  return (
    <>
      <PageHeader
        title="Invoice Processing Agent"
        subtitle="Drop a freight-invoice PDF. It's saved to the pil_workshop volume, parsed with ai_parse_document, and extracted via ai_extract + ai_query into structured fields, line items, and an exception flag."
      />

      <div
        className={`dropzone${dragging ? " drag" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          onPick(e.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
        onClick={() => !upload.isPending && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !upload.isPending)
            inputRef.current?.click();
        }}
      >
        <div className="dropzone-emoji">{upload.isPending ? "⏳" : "🧾"}</div>
        <div className="dropzone-title">
          {upload.isPending ? "Uploading & extracting…" : "Drop a PDF invoice here"}
        </div>
        <p className="muted" style={{ margin: "6px 0 16px" }}>
          or click to browse — try the samples in{" "}
          <code>~/Desktop/pil_invoice_samples</code>
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf,.pdf"
          style={{ display: "none" }}
          onChange={(e) => onPick(e.target.files)}
        />
        <button
          className="btn btn-primary"
          disabled={upload.isPending}
          onClick={(e) => {
            e.stopPropagation();
            inputRef.current?.click();
          }}
        >
          {upload.isPending ? "Processing…" : "Choose a PDF invoice"}
        </button>
        {fileName && (
          <p className="muted" style={{ marginTop: 12 }}>
            {upload.isPending ? "Uploading & extracting: " : "Last file: "}
            <strong>{fileName}</strong>
          </p>
        )}
      </div>

      {upload.isPending && (
        <div className="card">
          <Skeleton height={22} width="40%" />
          <div style={{ height: 12 }} />
          <Skeleton height={120} />
        </div>
      )}

      {!upload.isPending && result && <ResultCard r={result} />}

      {!upload.isPending && !result && (
        <EmptyState
          emoji="🧾"
          text="Upload an invoice to see its structured extraction here."
        />
      )}
    </>
  );
}

function ResultCard({ r }: { r: ExtractedInvoice }) {
  const computed = (r.subtotal ?? 0) + (r.tax ?? 0);
  const mismatch = r.exception_type === "total_mismatch";
  return (
    <div className="split">
      <div className="card">
        <h2 className="section-title">
          Extracted fields <ExceptionBadge type={r.exception_type} />
        </h2>
        <Field label="Invoice No" value={r.invoice_no ?? "—"} />
        <Field label="Customer" value={r.customer ?? "—"} />
        <Field label="PO Number" value={r.po_number ?? "— (missing)"} />
        <Field label="Currency" value={r.currency ?? "—"} />
        <Field label="Date" value={r.date ?? "—"} />
        <Field label="Payment terms" value={r.payment_terms ?? "—"} />
        <div className="field-row">
          <span className="field-label">Subtotal</span>
          <span className="field-value">{currency(r.subtotal)}</span>
        </div>
        <div className="field-row">
          <span className="field-label">Tax</span>
          <span className="field-value">{currency(r.tax)}</span>
        </div>
        <div className="field-row">
          <span className="field-label">Total</span>
          <span className={`field-value${mismatch ? " mismatch" : ""}`}>
            {currency(r.total)}
          </span>
        </div>
        {mismatch && (
          <p className="muted" style={{ color: "var(--neg)", marginTop: 8 }}>
            ⚠ Total {currency(r.total)} ≠ subtotal + tax ({currency(computed)}).
          </p>
        )}
        <p className="muted" style={{ marginTop: 12 }}>
          Saved to <code>{r.volume_path}</code>
        </p>
      </div>

      <div className="card">
        <h2 className="section-title">Line items ({r.line_items.length})</h2>
        {r.line_items.length === 0 ? (
          <EmptyState emoji="—" text="No line items extracted." />
        ) : (
          <table className="data">
            <thead>
              <tr>
                <th>Description</th>
                <th style={{ textAlign: "right" }}>Amount</th>
              </tr>
            </thead>
            <tbody>
              {r.line_items.map((li, i) => (
                <tr key={i}>
                  <td>{li.description ?? "—"}</td>
                  <td style={{ textAlign: "right" }}>{currency(li.amount)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
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
