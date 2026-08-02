import { PageHeader } from "../components/ui";

export function About() {
  return (
    <>
      <PageHeader
        title="About this app"
        subtitle="The architecture you just built in Part 2 of the workshop."
      />
      <div className="card" style={{ maxWidth: 820 }}>
        <h2 className="section-title">Architecture</h2>
        <pre
          style={{
            background: "var(--offwhite)",
            padding: 18,
            borderRadius: 8,
            overflowX: "auto",
            fontSize: 13,
            lineHeight: 1.6,
            color: "var(--ink)",
          }}
        >
          {`  React + Vite (this UI)
        │  /api
        ▼
  FastAPI backend  ───────────────┐
        │                         │
        ├─ Databricks SQL ──▶ Unity Catalog (gold KPIs, usage views)
        │                         │
        ├─ Lakebase (Postgres) ──▶ review queue · decisions · work orders
        │        │
        │        └─ synced table ─▶ gold.invoice_decisions_synced
        │
        └─ FMAPI via Unity AI Gateway
                 ├─ text endpoint   (invoice extraction)
                 └─ vision endpoint (container inspection)
                        │
                        ▼
             Dashboard Page 4 — AI usage & governance`}
        </pre>
        <ul className="muted" style={{ lineHeight: 1.9 }}>
          <li>
            <strong>Upload &amp; Extract:</strong> a PDF you upload is saved to the
            governed <code>bronze/raw_invoices</code> volume, parsed with
            <code> ai_parse_document</code>, then extracted with <code>ai_extract</code>{" "}
            (header fields) + <code>ai_query</code> (line items) into structured data.
          </li>
          <li>
            <strong>Governed AI:</strong> every model call routes through the same
            Unity-AI-Gateway FMAPI endpoints as the notebooks — no external providers,
            so the app's extraction is rate-limited, usage-tracked, and shows on Page 4.
          </li>
          <li>
            <strong>Reverse-ETL loop:</strong> analytical exceptions → Lakebase review
            queue → human decision → back to the gold layer.
          </li>
          <li>
            <strong>Auth:</strong> the app runs as its service principal;
            on-behalf-of-user calls use the forwarded access token. No PATs anywhere.
          </li>
        </ul>
      </div>
    </>
  );
}
