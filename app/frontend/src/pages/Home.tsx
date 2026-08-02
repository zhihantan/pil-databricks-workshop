import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import { PageHeader, Skeleton, StatTile } from "../components/ui";

export function Home() {
  const kpis = useQuery({ queryKey: ["kpis"], queryFn: api.kpis });
  const usage = useQuery({ queryKey: ["usage"], queryFn: api.usage });

  return (
    <>
      <PageHeader
        title="Operations Home"
        subtitle="Invoice processing, container inspections, and governed AI usage at a glance."
      />

      <div className="grid grid-4">
        {kpis.isLoading ? (
          Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="card">
              <Skeleton height={48} />
            </div>
          ))
        ) : (
          <>
            <StatTile
              label="Pending invoice reviews"
              value={kpis.data?.pending_reviews ?? 0}
            />
            <StatTile
              label="Containers inspected"
              value={kpis.data?.containers_inspected ?? 0}
            />
            <StatTile
              label="Schedule reliability"
              value={kpis.data?.schedule_reliability_pct ?? "—"}
              suffix="%"
            />
            <StatTile
              label="Vessel utilization"
              value={kpis.data?.vessel_utilization_pct ?? "—"}
              suffix="%"
            />
          </>
        )}
      </div>

      <div className="grid grid-2" style={{ marginTop: 18 }}>
        <div className="card">
          <h2 className="section-title">Governed AI usage (Unity AI Gateway)</h2>
          {usage.isLoading ? (
            <Skeleton height={160} />
          ) : (
            <>
              <div className="usage-row" style={{ marginBottom: 12 }}>
                <StatInline label="Tokens today" value={usage.data?.today_tokens ?? 0} />
                <StatInline label="Requests" value={usage.data?.today_requests ?? 0} />
                <StatInline
                  label="Est. cost"
                  value={`$${(usage.data?.today_cost_usd ?? 0).toFixed(2)}`}
                />
              </div>
              <ResponsiveContainer width="100%" height={150}>
                <AreaChart data={usage.data?.series ?? []}>
                  <defs>
                    <linearGradient id="tealGrad" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#0E7C86" stopOpacity={0.5} />
                      <stop offset="100%" stopColor="#0E7C86" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="usage_date" hide />
                  <YAxis hide />
                  <Tooltip />
                  <Area
                    type="monotone"
                    dataKey="total_tokens"
                    stroke="#0E7C86"
                    strokeWidth={2}
                    fill="url(#tealGrad)"
                  />
                </AreaChart>
              </ResponsiveContainer>
              <p className="muted">
                Traffic from notebooks and this app share the same governed endpoints —
                this widget mirrors dashboard Page 4.
              </p>
            </>
          )}
        </div>

        <div className="card">
          <h2 className="section-title">What you built</h2>
          <ol className="muted" style={{ lineHeight: 1.9, paddingLeft: 18 }}>
            <li>Medallion shipping data → Gold KPIs & metric views</li>
            <li>AI/BI dashboard + Genie space over the gold layer</li>
            <li>Invoice extraction & container vision on governed FMAPI</li>
            <li>This app: Lakebase review queue + work orders</li>
            <li>Forecasting & route optimization (ML notebooks)</li>
          </ol>
        </div>
      </div>
    </>
  );
}

function StatInline({ label, value }: { label: string; value: string | number }) {
  return (
    <div>
      <div className="muted">{label}</div>
      <div style={{ fontSize: 22, fontWeight: 700, color: "var(--navy)" }}>{value}</div>
    </div>
  );
}
