import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import { api } from "../api/client";
import type { ContainerAnalysis, InspectionItem } from "../api/types";
import { useToast } from "../components/Toast";
import {
  ConfidenceChip,
  DamageBadge,
  EmptyState,
  PageHeader,
  Skeleton,
  StatTile,
} from "../components/ui";

type DamageFilter = "all" | "none" | "minor" | "major";
type SortKey = "worst" | "least_confident" | "default";

const _damageRank = (d?: string | null): number =>
  ({ major: 3, minor: 2, none: 1 })[(d ?? "").toLowerCase()] ?? 0;

export function Inspections() {
  const qc = useQueryClient();
  const toast = useToast();
  const inputRef = useRef<HTMLInputElement>(null);
  const [active, setActive] = useState<InspectionItem | null>(null);
  const [dragging, setDragging] = useState(false);
  const [uploadResult, setUploadResult] = useState<ContainerAnalysis | null>(null);
  const [damageFilter, setDamageFilter] = useState<DamageFilter>("all");
  const [maxConf, setMaxConf] = useState<number>(1);
  const [sort, setSort] = useState<SortKey>("default");

  const list = useQuery({ queryKey: ["inspections"], queryFn: api.listInspections });
  const accuracy = useQuery({
    queryKey: ["inspection-accuracy"],
    queryFn: api.inspectionAccuracy,
  });

  const upload = useMutation({
    mutationFn: (f: File) => api.uploadContainer(f),
    onSuccess: (data) => {
      setUploadResult(data);
      toast.push(
        data.damage && data.damage !== "none"
          ? `Analyzed — ${data.damage} damage`
          : "Analyzed — no damage",
        data.damage && data.damage !== "none" ? "info" : "success",
      );
      qc.invalidateQueries({ queryKey: ["inspections"] });
    },
    onError: (e: Error) => toast.push(e.message, "error"),
  });

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

  const items = useMemo(() => list.data ?? [], [list.data]);
  const norm = (d?: string | null) => (d ?? "").toLowerCase();
  const majorCount = items.filter((i) => norm(i.damage) === "major").length;
  const minorCount = items.filter((i) => norm(i.damage) === "minor").length;
  const acc = accuracy.data;

  const shown = useMemo(() => {
    let out = items.filter((i) => {
      if (damageFilter !== "all" && norm(i.damage) !== damageFilter) return false;
      if (i.confidence != null && i.confidence > maxConf) return false;
      return true;
    });
    if (sort === "worst") {
      out = [...out].sort((a, b) => _damageRank(b.damage) - _damageRank(a.damage));
    } else if (sort === "least_confident") {
      out = [...out].sort((a, b) => (a.confidence ?? 1) - (b.confidence ?? 1));
    }
    return out;
  }, [items, damageFilter, maxConf, sort]);

  const onPick = (files: FileList | null) => {
    const f = files?.[0];
    if (!f) return;
    setUploadResult(null);
    upload.mutate(f);
  };

  return (
    <>
      <PageHeader
        title="Container Analysis Agent"
        subtitle="Multimodal vision inspects each container image through the governed FMAPI endpoint — classifying damage, scoring confidence, and recommending an action. Upload a photo to analyze one live, or review the batch below."
      />

      {/* Upload a container image for live analysis */}
      <div
        className={`dropzone${dragging ? " drag" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragging(false);
          onPick(e.dataTransfer.files);
        }}
        role="button"
        tabIndex={0}
        onClick={() => !upload.isPending && inputRef.current?.click()}
        onKeyDown={(e) => {
          if ((e.key === "Enter" || e.key === " ") && !upload.isPending)
            inputRef.current?.click();
        }}
      >
        <div className="dropzone-emoji">{upload.isPending ? "⏳" : "📦"}</div>
        <div className="dropzone-title">
          {upload.isPending ? "Analyzing image…" : "Drop a container photo here"}
        </div>
        <p className="muted" style={{ margin: "6px 0 16px" }}>
          PNG/JPG — the governed vision endpoint classifies damage live
        </p>
        <input
          ref={inputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,.png,.jpg,.jpeg,.webp"
          style={{ display: "none" }}
          onChange={(e) => onPick(e.target.files)}
        />
        <button
          className="btn btn-primary"
          disabled={upload.isPending}
          onClick={(e) => {
            e.stopPropagation();
            inputRef.current?.click();
          }}
        >
          {upload.isPending ? "Analyzing…" : "Choose an image"}
        </button>
      </div>

      {uploadResult && <UploadResult r={uploadResult} />}

      {/* KPI + accuracy strip */}
      {!list.isLoading && items.length > 0 && (
        <div className="grid grid-4" style={{ marginTop: 18, marginBottom: 18 }}>
          <StatTile label="Containers analyzed" value={items.length} />
          <StatTile label="Major damage" value={majorCount} />
          <StatTile label="Minor damage" value={minorCount} />
          <StatTile
            label="Accuracy vs labels"
            value={acc?.accuracy_pct != null ? acc.accuracy_pct : "—"}
            suffix={acc?.accuracy_pct != null ? "%" : undefined}
          />
        </div>
      )}

      {acc && acc.scored > 0 && Object.keys(acc.confusions).length > 0 && (
        <div className="card" style={{ marginBottom: 18 }}>
          <h2 className="section-title">
            Where the agent disagrees with ground truth ({acc.correct}/{acc.scored} correct)
          </h2>
          <div className="chip-list">
            {Object.entries(acc.confusions)
              .sort((a, b) => b[1] - a[1])
              .map(([k, n]) => (
                <span key={k} className="chip-tag" title="predicted → actual">
                  {k}: {n}
                </span>
              ))}
          </div>
        </div>
      )}

      {/* Filters + sort */}
      {!list.isLoading && items.length > 0 && (
        <div className="filter-bar">
          <label>
            Damage
            <select
              value={damageFilter}
              onChange={(e) => setDamageFilter(e.target.value as DamageFilter)}
            >
              <option value="all">All</option>
              <option value="major">Major</option>
              <option value="minor">Minor</option>
              <option value="none">None</option>
            </select>
          </label>
          <label>
            Max confidence
            <select value={maxConf} onChange={(e) => setMaxConf(Number(e.target.value))}>
              <option value={1}>Any</option>
              <option value={0.9}>&lt; 0.90</option>
              <option value={0.8}>&lt; 0.80</option>
              <option value={0.7}>&lt; 0.70</option>
            </select>
          </label>
          <label>
            Sort
            <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)}>
              <option value="default">Default</option>
              <option value="worst">Worst damage first</option>
              <option value="least_confident">Least confident first</option>
            </select>
          </label>
          <span className="muted" style={{ fontSize: 12, marginLeft: "auto" }}>
            {shown.length} of {items.length}
          </span>
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
        <EmptyState emoji="📦" text="No inspections yet. Run notebooks 07–08, or upload one above." />
      ) : shown.length === 0 ? (
        <EmptyState emoji="🔍" text="No containers match the current filters." />
      ) : (
        <div className="gallery">
          {shown.map((it) => (
            <div key={it.file_name} className="tile" onClick={() => setActive(it)}>
              {it.image_url ? (
                <img src={it.image_url} alt={it.container_no ?? it.file_name} loading="lazy" />
              ) : (
                <div style={{ height: 150, background: "var(--bg-2)" }} />
              )}
              <div className="tile-body">
                <span style={{ fontWeight: 600, fontSize: 13 }}>
                  {it.container_no ?? "—"}
                </span>
                <span style={{ display: "flex", gap: 6, alignItems: "center" }}>
                  <DamageBadge damage={it.damage} />
                  {it.is_correct === false && (
                    <span className="save-warn" title={`actual: ${it.gt_damage}`}>
                      ✗
                    </span>
                  )}
                </span>
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
            {active.gt_damage != null && (
              <Field
                label="Actual (ground truth)"
                value={`${active.gt_damage}${active.is_correct ? " ✓" : " ✗ mismatch"}`}
              />
            )}
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

function UploadResult({ r }: { r: ContainerAnalysis }) {
  const m = r.metrics;
  const flagged = r.damage != null && r.damage !== "none";
  return (
    <div className="card" style={{ marginTop: 16 }}>
      <div className="split">
        <div>
          {r.image_url && (
            <img
              src={r.image_url}
              alt={r.file_name}
              style={{ width: "100%", borderRadius: 8 }}
            />
          )}
        </div>
        <div>
          <h2 className="section-title">
            Analysis <DamageBadge damage={(r.damage as never) ?? null} />
          </h2>
          <Field label="Damage" value={r.damage ?? "—"} />
          <Field label="Damage type" value={r.damage_type ?? "—"} />
          <Field
            label="Confidence"
            value={r.confidence != null ? `${(r.confidence * 100).toFixed(0)}%` : "—"}
          />
          <Field label="Recommended action" value={r.recommended_action ?? "—"} />
          {m && (
            <div className="metrics-bar" style={{ marginTop: 14 }}>
              <div className="metric">
                <span className="metric-val">{(m.duration_ms / 1000).toFixed(1)}s</span>
                <span className="metric-lbl">Duration</span>
              </div>
              <div className="metric">
                <span className="metric-val">{m.est_total_tokens.toLocaleString()}</span>
                <span className="metric-lbl">Tokens</span>
                <span className="metric-est">est</span>
              </div>
              <div className="metric">
                <span className="metric-val">${m.est_cost_usd.toFixed(4)}</span>
                <span className="metric-lbl">Est. cost</span>
              </div>
              <div className="metric">
                <span className="metric-val">
                  {(m.model_endpoint ?? "—").replace("databricks-", "")}
                </span>
                <span className="metric-lbl">Model</span>
              </div>
            </div>
          )}
          {flagged && (
            <p className="muted" style={{ marginTop: 10, fontSize: 12 }}>
              ⚑ Flagged — open it in the gallery below to raise a work order.
            </p>
          )}
        </div>
      </div>
    </div>
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
