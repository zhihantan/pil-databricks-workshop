# Workspace Instructions for Genie Code (optional)

Optional workspace-level instructions for Genie Code when working in the
`pil_workshop` catalog. If your workspace supports workspace instructions, paste
this into **Settings → Genie Code → Workspace instructions**. Otherwise the
`pil-dashboard-builder` skill covers the same guidance at the agent level.

---

When working in the `pil_workshop` catalog (Pacific International Lines shipping
data):

- **Only** read from the `gold` schema for analytics: the `mv_*` materialized
  views and `metric_*` metric views. Do not query `bronze` or `silver` directly.
- Use metric views (`MEASURE(...)`) for ratio KPIs so definitions stay
  consistent across dashboards and Genie.
- A voyage leg is **late** when ATA > ETA + 24h. Schedule reliability % =
  on-time legs / total legs.
- Vessel utilization % = loaded TEU / capacity TEU (manifest-based).
- Default to the **last 12 months** unless the user specifies a period.
- Apply the PIL palette: deep navy `#0B1F3A`, ocean teal `#0E7C86`, signal amber
  `#F5A623`, off-white `#F7F9FB` background. Coral `#E4572E` only for
  negative/alert series.
- Prefer the `pil-dashboard-builder` skill for any dashboard-building task.
