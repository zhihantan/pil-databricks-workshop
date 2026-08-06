// Content-shaped skeleton screens shown while a page's code chunk and/or its
// data query loads. They mirror each page's real layout (same grid/card/split
// structure) so the transition to loaded content is jank-free — the shimmer
// blocks sit exactly where the real content lands.
//
// All blocks reuse the shared `.skeleton` shimmer primitive (see theme.css) via
// the <SkelLine>/<SkelBlock> atoms, so there's one animation and one source of
// truth for the loading look.

import type { CSSProperties } from "react";

/** A shimmer line — width is a % or px string, height in px. */
export function SkelLine({
  w = "100%",
  h = 14,
  style,
}: {
  w?: string;
  h?: number;
  style?: CSSProperties;
}) {
  return (
    <div
      className="skeleton"
      style={{ width: w, height: h, borderRadius: 6, ...style }}
    />
  );
}

/** A larger shimmer block (image/chart placeholder). */
export function SkelBlock({ h = 120, style }: { h?: number; style?: CSSProperties }) {
  return <div className="skeleton" style={{ height: h, borderRadius: 10, ...style }} />;
}

/** A card-shaped shimmer container with arbitrary children. */
function SkelCard({ children, style }: { children?: React.ReactNode; style?: CSSProperties }) {
  return (
    <div className="card" style={style}>
      {children}
    </div>
  );
}

/** The page header (title + subtitle) shimmer, shared by every page skeleton. */
function SkelHeader() {
  return (
    <div style={{ marginBottom: 22 }}>
      <SkelLine w="280px" h={30} style={{ marginBottom: 12 }} />
      <SkelLine w="60%" h={14} />
    </div>
  );
}

/** Generic fallback (used for lightweight/rarely-seen routes like About). */
export function PageSkeleton() {
  return (
    <>
      <SkelHeader />
      <SkelCard style={{ marginBottom: 18 }}>
        <SkelLine w="40%" h={20} style={{ marginBottom: 16 }} />
        <SkelLine style={{ marginBottom: 10 }} />
        <SkelLine w="90%" style={{ marginBottom: 10 }} />
        <SkelLine w="75%" />
      </SkelCard>
    </>
  );
}

/** Home: two hero agent cards + a 4-tile KPI strip. */
export function HomeSkeleton() {
  return (
    <>
      <SkelHeader />
      <div className="hero-grid">
        {[0, 1].map((i) => (
          <SkelCard key={i} style={{ minHeight: 220 }}>
            <SkelBlock h={40} style={{ width: 40, marginBottom: 18 }} />
            <SkelLine w="70%" h={20} style={{ marginBottom: 12 }} />
            <SkelLine style={{ marginBottom: 8 }} />
            <SkelLine w="85%" style={{ marginBottom: 24 }} />
            <div style={{ display: "flex", gap: 28 }}>
              <SkelLine w="90px" h={38} />
              <SkelLine w="90px" h={38} />
            </div>
          </SkelCard>
        ))}
      </div>
      <div className="grid grid-4">
        {[0, 1, 2, 3].map((i) => (
          <SkelCard key={i}>
            <SkelLine w="60%" h={12} style={{ marginBottom: 14 }} />
            <SkelLine w="45%" h={28} />
          </SkelCard>
        ))}
      </div>
    </>
  );
}

/** InvoiceReview: left PDF pane + right fields/queue split. */
export function InvoiceReviewSkeleton() {
  return (
    <>
      <SkelHeader />
      <div className="split">
        <SkelCard>
          <SkelLine w="50%" h={16} style={{ marginBottom: 16 }} />
          <SkelBlock h={520} />
        </SkelCard>
        <div>
          <SkelCard style={{ marginBottom: 16 }}>
            <SkelLine w="45%" h={20} style={{ marginBottom: 18 }} />
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}
              >
                <SkelLine w="30%" />
                <SkelLine w="40%" />
              </div>
            ))}
            <div style={{ display: "flex", gap: 10, marginTop: 20 }}>
              <SkelLine w="120px" h={38} />
              <SkelLine w="90px" h={38} />
            </div>
          </SkelCard>
          <SkelCard>
            <SkelLine w="40%" h={18} style={{ marginBottom: 16 }} />
            {[0, 1, 2, 3].map((i) => (
              <SkelLine key={i} h={18} style={{ marginBottom: 12 }} />
            ))}
          </SkelCard>
        </div>
      </div>
    </>
  );
}

/** Inspections: dropzone + KPI strip + image gallery grid. */
export function InspectionsSkeleton() {
  return (
    <>
      <SkelHeader />
      <SkelBlock h={150} style={{ marginBottom: 18 }} />
      <div className="grid grid-4" style={{ marginBottom: 18 }}>
        {[0, 1, 2, 3].map((i) => (
          <SkelCard key={i}>
            <SkelLine w="60%" h={12} style={{ marginBottom: 14 }} />
            <SkelLine w="40%" h={28} />
          </SkelCard>
        ))}
      </div>
      <div className="gallery">
        {Array.from({ length: 8 }).map((_, i) => (
          <div key={i} className="tile">
            <SkelBlock h={150} style={{ borderRadius: 0 }} />
            <div className="tile-body">
              <SkelLine w="60%" h={16} />
            </div>
          </div>
        ))}
      </div>
    </>
  );
}

/** Gateway: 3 stat tiles + a wide chart card. */
export function GatewaySkeleton() {
  return (
    <>
      <SkelHeader />
      <div className="grid grid-3">
        {[0, 1, 2].map((i) => (
          <SkelCard key={i}>
            <SkelLine w="55%" h={12} style={{ marginBottom: 14 }} />
            <SkelLine w="40%" h={28} />
          </SkelCard>
        ))}
      </div>
      <SkelCard style={{ marginTop: 18 }}>
        <SkelLine w="35%" h={18} style={{ marginBottom: 18 }} />
        <SkelBlock h={220} />
      </SkelCard>
    </>
  );
}
