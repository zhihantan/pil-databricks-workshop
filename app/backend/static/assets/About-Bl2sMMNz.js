import{j as e}from"./index-BSapqz82.js";import{P as s}from"./ui-ChY1ol96.js";function a(){return e.jsxs(e.Fragment,{children:[e.jsx(s,{title:"About this app",subtitle:"The architecture you just built in Part 2 of the workshop."}),e.jsxs("div",{className:"card",style:{maxWidth:820},children:[e.jsx("h2",{className:"section-title",children:"Architecture"}),e.jsx("pre",{style:{background:"var(--surface-2)",padding:18,borderRadius:8,overflowX:"auto",fontSize:13,lineHeight:1.6,color:"var(--ink)"},children:`  React + Vite (this UI)
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
             Dashboard Page 4 — AI usage & governance`}),e.jsxs("ul",{className:"muted",style:{lineHeight:1.9},children:[e.jsxs("li",{children:[e.jsx("strong",{children:"Upload & Extract:"})," a PDF you upload is saved to the governed ",e.jsx("code",{children:"bronze/raw_invoices"})," volume, parsed with",e.jsx("code",{children:" ai_parse_document"}),", then extracted with ",e.jsx("code",{children:"ai_extract"})," ","(header fields) + ",e.jsx("code",{children:"ai_query"})," (line items) into structured data."]}),e.jsxs("li",{children:[e.jsx("strong",{children:"Governed AI:"})," every model call routes through the same Unity-AI-Gateway FMAPI endpoints as the notebooks — no external providers, so the app's extraction is rate-limited, usage-tracked, and shows on Page 4."]}),e.jsxs("li",{children:[e.jsx("strong",{children:"Reverse-ETL loop:"})," analytical exceptions → Lakebase review queue → human decision → back to the gold layer."]}),e.jsxs("li",{children:[e.jsx("strong",{children:"Auth:"})," the app runs as its service principal; on-behalf-of-user calls use the forwarded access token. No PATs anywhere."]})]})]})]})}export{a as About};
