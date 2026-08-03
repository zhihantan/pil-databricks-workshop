import { useMutation } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ExtractedInvoice, ExtractionMetrics } from "../api/types";
import { useToast } from "../components/Toast";
import { currency, EmptyState, ExceptionBadge, PageHeader } from "../components/ui";

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
        subtitle="Drop an invoice PDF. It's saved to the pil_workshop volume, parsed with ai_parse_document, and extracted via ai_extract + a governed ai_query UC function into ~20 structured fields, line items, and an exception flag."
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
          {upload.isPending ? "Processing invoice…" : "Drop a PDF invoice here"}
        </div>
        <p className="muted" style={{ margin: "6px 0 16px" }}>
          or click to browse
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
            {upload.isPending ? "Working on: " : "Last file: "}
            <strong>{fileName}</strong>
          </p>
        )}
      </div>

      {upload.isPending && <PipelineLoader fileName={fileName} />}

      {!upload.isPending && result && <ResultView r={result} />}

      {!upload.isPending && !result && (
        <EmptyState
          emoji="🧾"
          text="Upload an invoice to see its structured extraction here."
        />
      )}
    </>
  );
}

/* ---- Loading: animated pipeline stepper + live elapsed timer ---------- */
const STEPS = [
  { key: "upload", ico: "📤", title: "Upload to volume", sub: "Saving PDF to bronze/raw_invoices" },
  { key: "parse", ico: "📄", title: "Parse document", sub: "ai_parse_document → text" },
  { key: "extract", ico: "🏷️", title: "Extract fields", sub: "ai_extract → header struct" },
  { key: "query", ico: "🧠", title: "Structure with LLM", sub: "governed ai_query UC function" },
  { key: "assemble", ico: "✅", title: "Assemble & validate", sub: "derive totals + exceptions" },
];

function PipelineLoader({ fileName }: { fileName: string | null }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const t0 = performance.now();
    const id = setInterval(() => setElapsed(performance.now() - t0), 100);
    return () => clearInterval(id);
  }, []);

  // Advance the visual step over time (best-effort pacing; real timing returns
  // with the result). Roughly: upload<1s, parse~40%, extract/query the bulk.
  const secs = elapsed / 1000;
  const active =
    secs < 0.8 ? 0 : secs < 2.5 ? 1 : secs < 4.5 ? 2 : secs < 12 ? 3 : 4;

  return (
    <div className="card">
      <div className="pipeline">
        <div className="pipe-head">
          <div>
            <div className="section-title" style={{ margin: 0 }}>
              Running extraction pipeline
            </div>
            <div className="pipe-sub">
              {fileName ?? "invoice.pdf"} · governed by Unity AI Gateway
            </div>
          </div>
          <div className="pipe-timer">{secs.toFixed(1)}s</div>
        </div>

        <div className="pipe-bar">
          <span />
        </div>

        <div className="pipe-steps">
          {STEPS.map((s, i) => {
            const state = i < active ? "done" : i === active ? "active" : "";
            return (
              <div key={s.key} className={`pipe-step ${state}`}>
                <div className="pipe-ico">{i < active ? "✓" : s.ico}</div>
                <div className="pipe-body">
                  <div className="pipe-title">{s.title}</div>
                  <div className="pipe-sub">{s.sub}</div>
                </div>
                {i === active && <div className="pipe-spin" />}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

/* ---- Results: metrics bar + grouped rich fields ----------------------- */
function ResultView({ r }: { r: ExtractedInvoice }) {
  return (
    <>
      {r.metrics && <MetricsBar m={r.metrics} />}
      <RichResult r={r} />
    </>
  );
}

function MetricsBar({ m }: { m: ExtractionMetrics }) {
  const secs = (m.duration_ms / 1000).toFixed(1);
  return (
    <div className="metrics-bar">
      <Metric val={`${secs}s`} lbl="Duration" sub={`parse+LLM ${m.extract_ms} ms`} />
      <Metric
        val={m.est_total_tokens.toLocaleString()}
        lbl="Tokens"
        sub={`~${m.est_input_tokens.toLocaleString()} in / ${m.est_output_tokens.toLocaleString()} out`}
        est
      />
      <Metric val={`$${m.est_cost_usd.toFixed(4)}`} lbl="Est. cost" sub="blended $5/1M" est />
      <Metric val={String(m.field_count)} lbl="Fields" sub="populated" />
      <Metric val={String(m.line_item_count)} lbl="Line items" sub="extracted" />
      <Metric
        val={(m.model_endpoint ?? "—").replace("databricks-", "")}
        lbl="Model"
        sub="via AI Gateway"
      />
    </div>
  );
}

function Metric({
  val,
  lbl,
  sub,
  est,
}: {
  val: string;
  lbl: string;
  sub?: string;
  est?: boolean;
}) {
  return (
    <div className="metric">
      <span className="metric-val">{val}</span>
      <span className="metric-lbl">{lbl}</span>
      {sub && <span className="metric-est">{est ? "est · " : ""}{sub}</span>}
    </div>
  );
}

function RichResult({ r }: { r: ExtractedInvoice }) {
  const computed = (r.subtotal ?? 0) + (r.tax ?? 0);
  const mismatch = r.exception_type === "total_mismatch";
  const cur = r.currency ?? "";
  return (
    <div className="split">
      <div>
        <div className="card">
          <h2 className="section-title">
            Extracted fields <ExceptionBadge type={r.exception_type} />
          </h2>

          <FieldGroup title="Invoice">
            <F l="Invoice No" v={r.invoice_no} />
            <F l="Invoice Date" v={r.invoice_date} />
            <F l="Due Date" v={r.due_date} />
            <F l="PO Number" v={r.purchase_order} missing="— (missing)" />
            <F l="Incoterms" v={r.incoterms} />
          </FieldGroup>

          <FieldGroup title="Parties">
            <F l="Vendor" v={r.vendor_name} />
            <F l="Vendor Tax ID" v={r.vendor_tax_id} />
            <F l="Customer" v={r.customer_name} />
          </FieldGroup>

          {(r.bill_of_lading ||
            r.vessel_name ||
            r.port_of_loading ||
            r.container_numbers.length > 0) && (
            <FieldGroup title="Shipping">
              <F l="Bill of Lading" v={r.bill_of_lading} />
              <F l="Vessel" v={r.vessel_name} />
              <F l="Port of Loading" v={r.port_of_loading} />
              <F l="Port of Discharge" v={r.port_of_discharge} />
              {r.container_numbers.length > 0 && (
                <div className="field-row" style={{ alignItems: "flex-start" }}>
                  <span className="field-label">Containers</span>
                  <span className="chip-list" style={{ justifyContent: "flex-end" }}>
                    {r.container_numbers.map((c) => (
                      <span key={c} className="chip-tag">
                        {c}
                      </span>
                    ))}
                  </span>
                </div>
              )}
            </FieldGroup>
          )}

          <FieldGroup title="Amounts">
            <Money l="Subtotal" v={r.subtotal} cur={cur} />
            {r.discount != null && <Money l="Discount" v={r.discount} cur={cur} />}
            {r.shipping != null && <Money l="Shipping" v={r.shipping} cur={cur} />}
            <Money l={`Tax${r.tax_rate ? ` (${r.tax_rate})` : ""}`} v={r.tax} cur={cur} />
            <div className="field-row">
              <span className="field-label">Total</span>
              <span className={`field-value${mismatch ? " mismatch" : ""}`}>
                {currency(r.total)} {cur}
              </span>
            </div>
            {r.amount_paid != null && <Money l="Amount Paid" v={r.amount_paid} cur={cur} />}
            {r.balance_due != null && <Money l="Balance Due" v={r.balance_due} cur={cur} />}
          </FieldGroup>

          {mismatch && (
            <p className="muted" style={{ color: "var(--neg)", marginTop: 4 }}>
              ⚠ Total {currency(r.total)} doesn't reconcile with subtotal, discount,
              shipping and tax ({currency(computed)} before adjustments).
            </p>
          )}
          {(r.payment_terms || r.bank_details || r.notes) && (
            <FieldGroup title="Terms & notes">
              <F l="Payment terms" v={r.payment_terms} />
              <F l="Bank" v={r.bank_details} />
              <F l="Notes" v={r.notes} />
            </FieldGroup>
          )}
          <div className="save-status">
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>
              📄 PDF saved to <code>{r.volume_path}</code>
            </p>
            {r.saved_table ? (
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12 }}>
                <span className="save-ok">✓ Delta</span> row written to{" "}
                <code>{r.saved_table.replace(/`/g, "")}</code>
              </p>
            ) : (
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12 }}>
                <span className="save-warn">⚠</span> Not persisted to Delta (extraction
                still returned).
              </p>
            )}
            {r.queued_for_review && (
              <p className="muted" style={{ margin: "4px 0 0", fontSize: 12 }}>
                <span className="save-ok">✓ Review</span> flagged as{" "}
                <code>{r.exception_type}</code> — added to the Lakebase review queue.
              </p>
            )}
          </div>
        </div>
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
                <th style={{ textAlign: "right" }}>Qty</th>
                <th style={{ textAlign: "right" }}>Unit</th>
                <th style={{ textAlign: "right" }}>Amount</th>
              </tr>
            </thead>
            <tbody>
              {r.line_items.map((li, i) => (
                <tr key={i}>
                  <td>{li.description ?? "—"}</td>
                  <td style={{ textAlign: "right" }}>{li.quantity ?? "—"}</td>
                  <td style={{ textAlign: "right" }}>{currency(li.unit_price)}</td>
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

function FieldGroup({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="field-group">
      <p className="field-group-title">{title}</p>
      {children}
    </div>
  );
}

function F({ l, v, missing = "—" }: { l: string; v: string | null; missing?: string }) {
  return (
    <div className="field-row">
      <span className="field-label">{l}</span>
      <span className="field-value">{v ?? missing}</span>
    </div>
  );
}

function Money({ l, v, cur }: { l: string; v: number | null; cur: string }) {
  return (
    <div className="field-row">
      <span className="field-label">{l}</span>
      <span className="field-value">
        {currency(v)} {cur}
      </span>
    </div>
  );
}
