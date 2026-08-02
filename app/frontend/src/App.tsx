import { NavLink, Route, Routes } from "react-router-dom";
import { About } from "./pages/About";
import { Home } from "./pages/Home";
import { Inspections } from "./pages/Inspections";
import { InvoiceReview } from "./pages/InvoiceReview";
import { UploadExtract } from "./pages/UploadExtract";

const NAV = [
  { to: "/", label: "Home", icon: "🏠", end: true },
  { to: "/upload", label: "Upload & Extract", icon: "📤" },
  { to: "/invoices", label: "Invoice Review", icon: "🧾" },
  { to: "/inspections", label: "Container Inspections", icon: "📦" },
  { to: "/about", label: "About", icon: "🧭" },
];

export function App() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-badge">🚢</span> PIL Ops
        </div>
        {NAV.map((n) => (
          <NavLink
            key={n.to}
            to={n.to}
            end={n.end}
            className={({ isActive }) => `nav-link${isActive ? " active" : ""}`}
          >
            <span>{n.icon}</span>
            {n.label}
          </NavLink>
        ))}
        <div style={{ marginTop: "auto", fontSize: 12, color: "#7f93a6" }}>
          Governed by Unity AI Gateway
        </div>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/upload" element={<UploadExtract />} />
          <Route path="/invoices" element={<InvoiceReview />} />
          <Route path="/inspections" element={<Inspections />} />
          <Route path="/about" element={<About />} />
        </Routes>
      </main>
    </div>
  );
}
