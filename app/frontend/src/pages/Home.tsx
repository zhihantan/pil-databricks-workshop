import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import { PageHeader, Skeleton, StatTile } from "../components/ui";

export function Home() {
  const kpis = useQuery({ queryKey: ["kpis"], queryFn: api.kpis });
  const k = kpis.data;

  return (
    <>
      <PageHeader
        title="PIL AI Operations"
        subtitle="Two production AI agents for a container shipping line — invoice processing and container image analysis — built on Databricks and governed by Unity AI Gateway."
      />

      {/* Two agents front and center */}
      <div className="hero-grid">
        <AgentHero
          to="/invoices/upload"
          emoji="🧾"
          title="Invoice Processing Agent"
          desc="Upload freight invoices as PDFs. The agent parses (ai_parse_document), extracts fields and line items (ai_extract + ai_query), and flags exceptions."
          stats={
            kpis.isLoading
              ? null
              : [
                  { num: k?.invoices_processed ?? 0, lbl: "Invoices processed" },
                  { num: k?.pending_reviews ?? 0, lbl: "Pending review" },
                ]
          }
          cta="Process an invoice →"
        />
        <AgentHero
          to="/inspections"
          emoji="📦"
          title="Container Analysis Agent"
          desc="Multimodal vision inspects container images for structural damage — classifying none / minor / major with confidence and a recommended action."
          stats={
            kpis.isLoading
              ? null
              : [
                  { num: k?.containers_inspected ?? 0, lbl: "Containers analyzed" },
                  {
                    num:
                      k?.inspection_accuracy_pct != null
                        ? `${k.inspection_accuracy_pct}%`
                        : "—",
                    lbl: "Accuracy vs truth",
                  },
                ]
          }
          cta="View inspections →"
        />
      </div>

      {/* Fleet KPI strip (context, not the focus) */}
      <div className="grid grid-4">
        {kpis.isLoading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="card">
              <Skeleton height={44} />
            </div>
          ))
        ) : (
          <>
            <StatTile label="Schedule reliability" value={k?.schedule_reliability_pct ?? "—"} suffix="%" />
            <StatTile label="Vessel utilization" value={k?.vessel_utilization_pct ?? "—"} suffix="%" />
            <StatTile label="Open work orders" value={k?.open_work_orders ?? 0} />
            <StatTile label="Invoices processed" value={k?.invoices_processed ?? 0} />
          </>
        )}
      </div>

      <div className="card" style={{ marginTop: 18 }}>
        <h2 className="section-title">Governed by Unity AI Gateway</h2>
        <p className="muted" style={{ margin: 0 }}>
          Both agents call the same governed Foundation Model endpoint — no external
          providers. Every request is rate-limited and usage-tracked.{" "}
          <Link to="/gateway">View AI Gateway usage →</Link>
        </p>
      </div>
    </>
  );
}

function AgentHero({
  to,
  emoji,
  title,
  desc,
  stats,
  cta,
}: {
  to: string;
  emoji: string;
  title: string;
  desc: string;
  stats: { num: number | string; lbl: string }[] | null;
  cta: string;
}) {
  return (
    <Link to={to} className="hero-card">
      <div className="hero-emoji">{emoji}</div>
      <h3 className="hero-title">{title}</h3>
      <p className="hero-desc">{desc}</p>
      <div className="hero-stats">
        {stats
          ? stats.map((s, i) => (
              <div key={i}>
                <div className="hero-stat-num">{s.num}</div>
                <div className="hero-stat-lbl">{s.lbl}</div>
              </div>
            ))
          : Array.from({ length: 2 }).map((_, i) => (
              <Skeleton key={i} height={38} width="90px" />
            ))}
      </div>
      <div className="hero-cta">{cta}</div>
    </Link>
  );
}
