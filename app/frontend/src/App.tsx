import { NavLink, Route, Routes } from "react-router-dom";
import { ThemeSwitcher } from "./components/Theme";
import { About } from "./pages/About";
import { Gateway } from "./pages/Gateway";
import { Home } from "./pages/Home";
import { Inspections } from "./pages/Inspections";
import { InvoiceReview } from "./pages/InvoiceReview";
import { UploadExtract } from "./pages/UploadExtract";

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
          <Route path="/" element={<Home />} />
          <Route path="/invoices/upload" element={<UploadExtract />} />
          <Route path="/invoices/review" element={<InvoiceReview />} />
          <Route path="/inspections" element={<Inspections />} />
          <Route path="/gateway" element={<Gateway />} />
          <Route path="/about" element={<About />} />
          {/* legacy paths */}
          <Route path="/upload" element={<UploadExtract />} />
          <Route path="/invoices" element={<InvoiceReview />} />
        </Routes>
      </main>
    </div>
  );
}
