import { lazy, Suspense, type ReactNode } from "react";
import { NavLink, Route, Routes, useLocation } from "react-router-dom";
import { ThemeSwitcher } from "./components/Theme";
import {
  GatewaySkeleton,
  HomeSkeleton,
  InspectionsSkeleton,
  InvoiceReviewSkeleton,
  PageSkeleton,
} from "./components/Skeletons";

// Route-level code splitting: each page is its own chunk, fetched on demand.
// This keeps the initial bundle small — notably, recharts (heavy, ~hundreds of
// KB) is only used by Gateway, so it now loads ONLY when that tab is opened.
const Home = lazy(() => import("./pages/Home").then((m) => ({ default: m.Home })));
const UploadExtract = lazy(() =>
  import("./pages/UploadExtract").then((m) => ({ default: m.UploadExtract })),
);
const InvoiceReview = lazy(() =>
  import("./pages/InvoiceReview").then((m) => ({ default: m.InvoiceReview })),
);
const Inspections = lazy(() =>
  import("./pages/Inspections").then((m) => ({ default: m.Inspections })),
);
const Gateway = lazy(() => import("./pages/Gateway").then((m) => ({ default: m.Gateway })));
const About = lazy(() => import("./pages/About").then((m) => ({ default: m.About })));

interface NavItem {
  to: string;
  label: string;
  icon: string;
  end?: boolean;
}

const NAV: { section: string; items: NavItem[] }[] = [
  {
    section: "Overview",
    items: [{ to: "/", label: "Home", icon: "🏠", end: true }],
  },
  {
    section: "AI Agents",
    items: [
      { to: "/invoices/upload", label: "Invoice Processing", icon: "🧾" },
      { to: "/invoices/review", label: "Review Queue", icon: "✅" },
      { to: "/inspections", label: "Container Analysis", icon: "📦" },
    ],
  },
  {
    section: "Governance",
    items: [
      { to: "/gateway", label: "AI Gateway Usage", icon: "📊" },
      { to: "/about", label: "About", icon: "🧭" },
    ],
  },
];

export function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-badge">🚢</span> PIL AI Ops
        </div>
        {NAV.map((group) => (
          <div key={group.section}>
            <div className="nav-section">{group.section}</div>
            {group.items.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
              >
                <span className="nav-ico">{n.icon}</span>
                {n.label}
              </NavLink>
            ))}
          </div>
        ))}
        <ThemeSwitcher />
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Lazy fallback={<HomeSkeleton />}><Home /></Lazy>} />
          <Route
            path="/invoices/upload"
            element={<Lazy fallback={<PageSkeleton />}><UploadExtract /></Lazy>}
          />
          <Route
            path="/invoices/review"
            element={<Lazy fallback={<InvoiceReviewSkeleton />}><InvoiceReview /></Lazy>}
          />
          <Route
            path="/inspections"
            element={<Lazy fallback={<InspectionsSkeleton />}><Inspections /></Lazy>}
          />
          <Route path="/gateway" element={<Lazy fallback={<GatewaySkeleton />}><Gateway /></Lazy>} />
          <Route path="/about" element={<Lazy fallback={<PageSkeleton />}><About /></Lazy>} />
          {/* legacy paths */}
          <Route
            path="/upload"
            element={<Lazy fallback={<PageSkeleton />}><UploadExtract /></Lazy>}
          />
          <Route
            path="/invoices"
            element={<Lazy fallback={<InvoiceReviewSkeleton />}><InvoiceReview /></Lazy>}
          />
        </Routes>
      </main>
    </div>
  );
}

/**
 * Suspense boundary for a lazily-loaded page. `key`ed on the current path so a
 * fresh boundary (and its skeleton fallback) shows on every route change while
 * the target page's chunk downloads — no blank flash between tabs.
 */
function Lazy({ fallback, children }: { fallback: ReactNode; children: ReactNode }) {
  const { pathname } = useLocation();
  return (
    <Suspense key={pathname} fallback={fallback}>
      {children}
    </Suspense>
  );
}
