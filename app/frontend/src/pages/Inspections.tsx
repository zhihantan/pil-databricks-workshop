import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api/client";
import type { InspectionItem } from "../api/types";
import { useToast } from "../components/Toast";
import {
  ConfidenceChip,
  DamageBadge,
  EmptyState,
  PageHeader,
  Skeleton,
  StatTile,
} from "../components/ui";

export function Inspections() {
  const qc = useQueryClient();
  const toast = useToast();
  const [active, setActive] = useState<InspectionItem | null>(null);

  const list = useQuery({ queryKey: ["inspections"], queryFn: api.listInspections });

  const refresh = useMutation({
    mutationFn: (fileName: string) => api.refreshInspection(fileName),
    onSuccess: () => {
      toast.push("Re-analyzed via governed vision endpoint", "success");
      qc.invalidateQueries({ queryKey: ["inspections"] });
    },
    onError: (e: Error) => toast.push(e.message, "error"),
  });

  const workOrder = useMutation({
    mutationFn: (it: InspectionItem) =>
      api.createWorkOrder({
        file_name: it.file_name,
        container_no: it.container_no,
        damage: it.damage,
        damage_type: it.damage_type,
        action: it.recommended_action ?? "Flag for manual inspection",
      }),
    onSuccess: () => {
      toast.push("Work order created", "success");
      qc.invalidateQueries({ queryKey: ["kpis"] });
    },
    onError: (e: Error) => toast.push(e.message, "error"),
  });

  const items = list.data ?? [];
  const norm = (d?: string | null) => (d ?? "").toLowerCase();
  const majorCount = items.filter((i) => norm(i.damage).includes("major")).length;
  const minorCount = items.filter((i) => norm(i.damage).includes("minor")).length;
  const flagged = majorCount + minorCount;

  return (
    <>
      <PageHeader
        title="Container Analysis Agent"
        subtitle="Multimodal vision inspects each container image through the governed FMAPI endpoint — classifying damage, scoring confidence, and recommending an action. Click a container to review or raise a work order."
      />

      {!list.isLoading && items.length > 0 && (
        <div className="grid grid-4" style={{ marginBottom: 18 }}>
          <StatTile label="Containers analyzed" value={items.length} />
          <StatTile label="Flagged for action" value={flagged} />
          <StatTile label="Major damage" value={majorCount} />
          <StatTile label="Minor damage" value={minorCount} />
        </div>
      )}

      {list.isLoading ? (
        <div className="gallery">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="tile">
              <Skeleton height={150} />
              <div className="tile-body">
                <Skeleton height={18} width="60%" />
              </div>
            </div>
          ))}
        </div>
      ) : items.length === 0 ? (
        <EmptyState emoji="📦" text="No inspections yet. Run notebooks 07–08." />
      ) : (
        <div className="gallery">
          {items.map((it) => (
            <div key={it.file_name} className="tile" onClick={() => setActive(it)}>
              {it.image_url ? (
                <img
                  src={it.image_url}
                  alt={it.container_no ?? it.file_name}
                  loading="lazy"
                />
              ) : (
                <div style={{ height: 150, background: "#dfe4e9" }} />
              )}
              <div className="tile-body">
                <span style={{ fontWeight: 600, fontSize: 13 }}>
                  {it.container_no ?? "—"}
                </span>
                <DamageBadge damage={it.damage} />
              </div>
            </div>
          ))}
        </div>
      )}

      {active && (
        <div className="drawer-overlay" onClick={() => setActive(null)}>
          <div className="drawer" onClick={(e) => e.stopPropagation()}>
            <h2 className="section-title">{active.container_no ?? active.file_name}</h2>
            {active.image_url && (
              <img
                src={active.image_url}
                alt={active.file_name}
                style={{ width: "100%", borderRadius: 8, marginBottom: 16 }}
              />
            )}
            <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
              <DamageBadge damage={active.damage} />
              <ConfidenceChip value={active.confidence} />
            </div>
            <Field label="Damage type" value={active.damage_type ?? "—"} />
            <Field label="Recommended action" value={active.recommended_action ?? "—"} />
            <Field label="File" value={active.file_name} />
            <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
              <button
                className="btn btn-amber"
                disabled={workOrder.isPending}
                onClick={() => workOrder.mutate(active)}
              >
                Create work order
              </button>
              <button
                className="btn btn-ghost"
                disabled={refresh.isPending}
                onClick={() => refresh.mutate(active.file_name)}
              >
                {refresh.isPending ? "Analyzing…" : "Re-analyze"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="field-row">
      <span className="field-label">{label}</span>
      <span className="field-value">{value}</span>
    </div>
  );
}
