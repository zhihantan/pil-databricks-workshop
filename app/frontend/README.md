# PIL App — Frontend (React + Vite + TypeScript)

Polished operations UI for invoice review and container inspections. Uses
TanStack Query for data, React Router for navigation, and Recharts for the small
KPI charts. The design system (workshop palette: deep navy `#0B1F3A`, ocean teal
`#0E7C86`, signal amber `#F5A623`, off-white background; Inter font) lives in
`src/design/theme.css`.

## Pages

- **Home** — KPI cards + governed AI-usage widget + "what you built".
- **Invoice Review** — split view: PDF preview left, extracted fields right with
  mismatch highlighting, inline adjustment, approve / reject / adjust.
- **Container Inspections** — image gallery with damage badges + confidence
  chips; detail drawer with model output and a "create work order" flow.
- **About** — architecture diagram of what participants built.

## Local development

```bash
cd app/frontend
npm install
npm run dev            # http://localhost:5173, proxies /api → :8000
```

Run the backend separately (`PIL_DEV_CORS=1 uvicorn backend.main:app --reload`).

## Build

```bash
npm run build          # tsc -b && vite build → ../backend/static
```

The build output goes straight into `app/backend/static`, which FastAPI serves.

## Quality

```bash
npm run lint           # ESLint (TypeScript + react-hooks)
npm run format         # Prettier
```

Accessibility: form controls are labeled, focus states preserved, and colors
meet contrast on the off-white background. Loading uses skeletons; empty and
error states are handled with toasts.
