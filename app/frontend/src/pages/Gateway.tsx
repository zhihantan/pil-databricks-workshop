import { useQuery } from "@tanstack/react-query";
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { api } from "../api/client";
import { PageHeader, Skeleton, StatTile } from "../components/ui";

export function Gateway() {
  const usage = useQuery({ queryKey: ["usage"], queryFn: api.usage });
  const u = usage.data;

  return (
    <>
      <PageHeader
        title="Unity AI Gateway Usage"
        subtitle="Every model call the agents make — invoice extraction and container analysis — is governed by Unity AI Gateway: rate-limited, usage-tracked, and audited. This is the same data on dashboard Page 4."
      />

      {usage.isLoading ? (
        <div className="grid grid-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="card">
              <Skeleton height={44} />
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-3">
            <StatTile label="Tokens today" value={(u?.today_tokens ?? 0).toLocaleString()} />
            <StatTile label="Requests today" value={u?.today_requests ?? 0} />
            <StatTile label="Est. cost today" value={`$${(u?.today_cost_usd ?? 0).toFixed(2)}`} />
          </div>

          <div className="card" style={{ marginTop: 18 }}>
            <h2 className="section-title">Tokens per day (last 30 days)</h2>
            {(u?.series?.length ?? 0) === 0 ? (
              <p className="muted">
                No governed traffic recorded yet. Run the invoice or container agents,
                then refresh — calls appear here once the gateway usage tables populate.
              </p>
            ) : (
              <ResponsiveContainer width="100%" height={220}>
                <AreaChart data={u?.series ?? []}>
                  <defs>
                    <linearGradient id="gw" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="var(--accent)" stopOpacity={0.5} />
                      <stop offset="100%" stopColor="var(--accent)" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="usage_date" tick={{ fontSize: 11 }} minTickGap={24} />
                  <YAxis tick={{ fontSize: 11 }} width={48} />
                  <Tooltip />
                  <Area
                    type="monotone"
                    dataKey="total_tokens"
                    stroke="var(--accent)"
                    strokeWidth={2}
                    fill="url(#gw)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            )}
          </div>

          <div className="card" style={{ marginTop: 18 }}>
            <h2 className="section-title">How governance works here</h2>
            <ul className="muted" style={{ lineHeight: 1.9, paddingLeft: 18, margin: 0 }}>
              <li>All agent model calls route through one governed FMAPI endpoint (no external providers).</li>
              <li>Per-user rate limits and usage tracking are enabled on that endpoint.</li>
              <li>Token/request/cost metrics flow to the gold usage views and dashboard Page 4.</li>
            </ul>
          </div>
        </>
      )}
    </>
  );
}
