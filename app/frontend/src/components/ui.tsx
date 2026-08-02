// Small shared UI atoms: page header, stat tile, badges, skeleton, empty state.
import type { ReactNode } from "react";
import type { Damage } from "../api/types";

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div>
      <h1 className="page-title">{title}</h1>
      {subtitle && <p className="page-subtitle">{subtitle}</p>}
    </div>
  );
}

export function StatTile({
  label,
  value,
  suffix,
}: {
  label: string;
  value: ReactNode;
  suffix?: string;
}) {
  return (
    <div className="card stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">
        {value}
        {suffix && <span style={{ fontSize: 16, color: "var(--slate)" }}> {suffix}</span>}
      </span>
      <span className="stat-accent" />
    </div>
  );
}

export function DamageBadge({ damage }: { damage: Damage | null }) {
  const map: Record<string, string> = {
    none: "badge-none",
    minor: "badge-minor",
    major: "badge-major",
  };
  const cls = damage ? (map[damage] ?? "badge-neutral") : "badge-neutral";
  const dot = damage === "major" ? "●" : damage === "minor" ? "◐" : "○";
  return (
    <span className={`badge ${cls}`}>
      {dot} {damage ?? "unknown"}
    </span>
  );
}

export function ConfidenceChip({ value }: { value: number | null }) {
  if (value == null) return null;
  return <span className="badge chip-confidence">{Math.round(value * 100)}% conf.</span>;
}

export function ExceptionBadge({ type }: { type: string | null }) {
  if (!type) return <span className="badge badge-none">clean</span>;
  const label = type.replace(/_/g, " ");
  return <span className="badge badge-major">{label}</span>;
}

export function Skeleton({
  height = 20,
  width = "100%",
}: {
  height?: number;
  width?: string;
}) {
  return <div className="skeleton" style={{ height, width }} />;
}

export function EmptyState({ emoji, text }: { emoji: string; text: string }) {
  return (
    <div className="empty">
      <div className="empty-emoji">{emoji}</div>
      <p>{text}</p>
    </div>
  );
}

export function currency(n: number | null | undefined): string {
  if (n == null) return "—";
  return n.toLocaleString(undefined, { style: "currency", currency: "USD" });
}
