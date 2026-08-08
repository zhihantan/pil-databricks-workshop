"""Deterministic synthetic-data generators for PIL's container liner business.

The single entry point :func:`generate_all` produces a coherent, internally
consistent dataset (foreign keys resolve, dates are ordered) from a
:class:`pil_workshop.config.DataScale`. Each sub-generator is independently
importable and unit-testable off-platform.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from ..config import DataScale, get_scale, history_window
from . import inventory, invoices, reference, transactions
from .iso6346 import container_number, is_valid

__all__ = [
    "reference",
    "transactions",
    "invoices",
    "inventory",
    "container_number",
    "is_valid",
    "generate_all",
    "generate_increment",
    "INCREMENTAL_TABLES",
    "GeneratedData",
]


class GeneratedData(dict):
    """A dict of ``entity_name -> list[dict]`` with a convenience row-count view."""

    def counts(self) -> dict[str, int]:
        return {k: len(v) for k, v in self.items()}


def generate_all(
    scale: DataScale | str | None = None,
    today: date | None = None,
    window_start: date | None = None,
) -> GeneratedData:
    """Generate the full coherent dataset for a scale.

    Order matters: reference entities first, then transactions that reference
    them, then invoices (from bookings) and inventory (independent).

    ``window_start`` (optional) narrows voyage departure dates to
    ``[window_start, end]`` (used by the incremental append path so the fresh
    slice is dated recently); ``None`` spreads them over the full history.
    """
    spec = scale if isinstance(scale, DataScale) else get_scale(scale)

    ports = reference.gen_ports(spec.ports)
    vessels = reference.gen_vessels(spec.vessels)
    routes = reference.gen_routes(spec.routes, ports)
    customers = reference.gen_customers(spec.customers, ports)

    voyages, legs = transactions.gen_voyages_and_legs(
        spec, vessels, routes, ports, today, window_start=window_start)
    containers = transactions.gen_containers(spec)
    bookings, shipments = transactions.gen_bookings_and_shipments(
        spec, voyages, legs, customers, containers, ports, today
    )
    events = transactions.gen_container_events(spec, shipments)
    port_calls = transactions.gen_port_calls(spec, voyages, legs)

    inv, line_items = invoices.gen_invoices(spec, bookings, customers, today)

    parts = inventory.gen_spare_parts(spec)
    consumption = inventory.gen_consumption(spec, parts, today)

    return GeneratedData(
        {
            "ports": ports,
            "vessels": vessels,
            "routes": routes,
            "customers": customers,
            "voyages": voyages,
            "voyage_legs": legs,
            "containers": containers,
            "bookings": bookings,
            "shipments": shipments,
            "container_events": events,
            "port_calls": port_calls,
            "invoices": inv,
            "invoice_line_items": line_items,
            "spare_parts": parts,
            "spare_parts_consumption": consumption,
        }
    )


# ---------------------------------------------------------------------------
# Incremental append support (for the 12-hourly job).
#
# STABLE dimension tables (ports, vessels, routes, customers, containers,
# spare_parts) are regenerated identically and overwrite each run. The
# event/transaction tables below GROW: each run generates a fresh batch and
# appends it. Voyages and voyage_legs are ALSO incremental (added so the fleet-
# ops KPIs, which date off voyage_legs.eta, advance each run) — they own their
# PKs and the append offsets those, plus every FK that points BETWEEN incremental
# tables. FKs that point at the FIXED dimensions (container_id, customer_id,
# vessel_id, route_id, *_port_id) are left UNCHANGED so they still resolve
# against the base load.
# ---------------------------------------------------------------------------
INCREMENTAL_TABLES: tuple[str, ...] = (
    "voyages",
    "voyage_legs",
    "bookings",
    "shipments",
    "container_events",
    "port_calls",
    "invoices",
    "invoice_line_items",
)

# Per table: integer PKs the table OWNS + integer FKs that reference ANOTHER
# incremental table (both get offset by the SAME shared id_offset, so a FK still
# points at its owner within the same batch). Everything else (fixed-dimension
# FKs, business strings, timestamps, measures) is left as generated.
_OFFSET_INT_FIELDS: dict[str, tuple[str, ...]] = {
    "voyages": ("voyage_id",),
    "voyage_legs": ("leg_id", "voyage_id"),               # voyage_id → voyages (incremental)
    "bookings": ("booking_id", "voyage_id", "leg_id"),    # voyage_id/leg_id → voyages/legs (incremental)
    "shipments": ("shipment_id", "booking_id"),           # booking_id → bookings (incremental)
    "container_events": ("event_id", "shipment_id"),      # shipment_id → shipments (incremental)
    "port_calls": ("port_call_id", "voyage_id"),          # voyage_id → voyages (incremental)
    "invoices": ("invoice_id", "booking_id"),             # booking_id → bookings (incremental)
    "invoice_line_items": ("line_item_id", "invoice_id"),  # invoice_id → invoices (incremental)
}
# Business-key strings that embed the PK and so must be re-suffixed to stay
# unique. Simple templates re-key off the (offset) owning PK; ``voyage_no`` keeps
# its per-row service-code prefix, so it is re-keyed specially below.
_OFFSET_STR_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "bookings": (("booking_no", "BKG{n:08d}"),),
    "invoices": (("invoice_no", "INV-{n:d}"),),  # base uses INV-<year>-<seq>; we re-key by offset id
}


def _daily_scale(spec: DataScale, days: float) -> DataScale:
    """Return a copy of ``spec`` with the event/transaction volumes scaled down to
    ``days`` worth of the ~24-month history (so one run ≈ one day of activity, not
    a full re-load). Voyages are scaled too (they are incremental — see
    ``INCREMENTAL_TABLES``). The fixed dimensions
    (vessels/ports/routes/customers/containers) stay full so the small batch's
    dimension FKs still reference the whole base universe.
    """
    from dataclasses import replace

    from ..config import HISTORY_MONTHS

    hist_days = max(1.0, HISTORY_MONTHS * 30.4375)
    frac = max(0.0, days) / hist_days
    scaled: dict[str, int] = {}
    for field in ("voyages", "bookings", "container_events", "port_calls", "invoices"):
        base = getattr(spec, field)
        # keep at least a handful so a slice is never empty; round to whole rows
        scaled[field] = max(5, round(base * frac))
    return replace(spec, name=f"{spec.name}_daily", **scaled)


def generate_increment(
    scale: DataScale | str | None = None,
    *,
    id_offset: int,
    days: float = 1.0,
    today: date | None = None,
) -> GeneratedData:
    """Generate a fresh, appendable batch of ONLY the event/transaction tables,
    sized to ``days`` of activity (default 1 day) rather than a full re-load.

    ``id_offset`` is added to every owned-PK / intra-event-FK integer (use the
    current max id across runs, e.g. ``MAX(id)`` read from Bronze) so appended
    rows never collide with prior runs. Dimension FKs are preserved so the new
    rows reference the existing base load. ``today`` anchors the batch's
    timestamps to the current run window; ``days`` controls the slice size
    (event volumes ≈ full × days / history-days).

    The batch is also DATE-CONFINED to the last ``days`` of the window (voyage
    departures sampled from ``[end - days, end]``), so the appended rows show up
    as recent activity — the dashboard's latest dates visibly grow each run —
    rather than being scattered across the full ~24-month history.

    Returns a GeneratedData holding only ``INCREMENTAL_TABLES``.
    """
    spec = scale if isinstance(scale, DataScale) else get_scale(scale)
    _, end = history_window(today)
    # Confine the slice to the recent window so growth is visible at the tail.
    window_start = end - timedelta(days=int(max(1, round(days))))
    full = generate_all(_daily_scale(spec, days), today=today, window_start=window_start)
    out = GeneratedData()
    for name in INCREMENTAL_TABLES:
        rows = [dict(r) for r in full.get(name, [])]
        int_fields = _OFFSET_INT_FIELDS.get(name, ())
        str_fields = _OFFSET_STR_FIELDS.get(name, ())
        for r in rows:
            for f in int_fields:
                if r.get(f) is not None:
                    r[f] = int(r[f]) + id_offset
            for f, tmpl in str_fields:
                # re-key the business string off the (now-offset) owning PK
                pk = int_fields[0]
                if r.get(pk) is not None:
                    r[f] = tmpl.format(n=r[pk])
            # voyage_no embeds the voyage_id after a per-row service-code prefix
            # (e.g. "SVC-00042"); re-suffix the numeric part off the offset id so
            # it stays unique without dropping the prefix.
            if name == "voyages" and r.get("voyage_id") is not None:
                prefix = str(r.get("voyage_no", "")).rsplit("-", 1)[0]
                r["voyage_no"] = f"{prefix}-{int(r['voyage_id']):05d}"
        out[name] = rows
    return out


def summarize(data: GeneratedData) -> dict[str, Any]:
    """Return quick sanity metrics facilitators can eyeball after generation."""
    legs = data.get("voyage_legs", [])
    on_time = sum(1 for leg in legs if leg.get("on_time"))
    reliability = (100.0 * on_time / len(legs)) if legs else 0.0
    return {
        "row_counts": data.counts(),
        "approx_schedule_reliability_pct": round(reliability, 1),
        "n_anomalous_invoices": sum(1 for i in data.get("invoices", []) if i.get("gt_anomaly")),
    }
