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

      <div className="usage-bar">
        <p className="usage-note">
          ⏱ Usage is sourced from Databricks system tables, which ingest with a
          delay — newly-made calls typically appear here about <strong>30 minutes</strong>{" "}
          after they happen.
        </p>
        <button
          className="btn btn-ghost btn-sm"
          onClick={() => usage.refetch()}
          disabled={usage.isFetching}
        >
          {usage.isFetching ? "Refreshing…" : "↻ Refresh"}
        </button>
      </div>

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
            <StatTile label="Total tokens (all-time)" value={(u?.all_time_tokens ?? 0).toLocaleString()} />
            <StatTile label="Total requests (all-time)" value={(u?.all_time_requests ?? 0).toLocaleString()} />
            <StatTile label="Est. cost (all-time)" value={`$${(u?.all_time_cost_usd ?? 0).toFixed(2)}`} />
          </div>

          {/* Per-endpoint all-time breakdown — which governed endpoints the
              agents use and how much of the token spend goes to each. */}
          <div className="card" style={{ marginTop: 18 }}>
            <h2 className="section-title">Usage by endpoint (all-time)</h2>
            {(u?.by_endpoint?.length ?? 0) === 0 ? (
              <p className="muted" style={{ margin: 0 }}>
                No per-endpoint usage yet — run the invoice or container agents, then
                refresh.
              </p>
            ) : (
              <table className="data">
                <thead>
                  <tr>
                    <th>Endpoint</th>
                    <th style={{ textAlign: "right" }}>Requests</th>
                    <th style={{ textAlign: "right" }}>Tokens</th>
                    <th style={{ textAlign: "right" }}>Est. cost</th>
                  </tr>
                </thead>
                <tbody>
                  {(u?.by_endpoint ?? []).map((e) => (
                    <tr key={e.endpoint}>
                      <td>{e.endpoint}</td>
                      <td style={{ textAlign: "right" }}>
                        {e.request_count.toLocaleString()}
                      </td>
                      <td style={{ textAlign: "right" }}>
                        {e.total_tokens.toLocaleString()}
                      </td>
                      <td style={{ textAlign: "right" }}>${e.est_cost_usd.toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
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
              <li>Both agents call the same governed FMAPI endpoint (no external providers).</li>
              <li>Rate limits and usage tracking are enabled on that endpoint via Unity AI Gateway.</li>
              <li>
                The figures above are scoped to this project&apos;s agents — filtered by
                endpoint <em>and</em> requester identity (the app&apos;s service principal
                and the setup user), since the underlying pay-per-token endpoint is shared
                across the workspace.
              </li>
              <li>Token/request/cost metrics flow to the gold usage views and dashboard Page 4.</li>
            </ul>
          </div>
        </>
      )}
    </>
  );
}
