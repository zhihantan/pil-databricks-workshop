"""Deterministic synthetic-data generators for PIL's container liner business.

The single entry point :func:`generate_all` produces a coherent, internally
consistent dataset (foreign keys resolve, dates are ordered) from a
:class:`pil_workshop.config.DataScale`. Each sub-generator is independently
importable and unit-testable off-platform.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from ..config import DataScale, get_scale
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


def generate_all(scale: DataScale | str | None = None, today: date | None = None) -> GeneratedData:
    """Generate the full coherent dataset for a scale.

    Order matters: reference entities first, then transactions that reference
    them, then invoices (from bookings) and inventory (independent).
    """
    spec = scale if isinstance(scale, DataScale) else get_scale(scale)

    ports = reference.gen_ports(spec.ports)
    vessels = reference.gen_vessels(spec.vessels)
    routes = reference.gen_routes(spec.routes, ports)
    customers = reference.gen_customers(spec.customers, ports)

    voyages, legs = transactions.gen_voyages_and_legs(spec, vessels, routes, ports, today)
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
# Reference/dimension tables (ports, vessels, routes, customers, containers,
# voyages, voyage_legs, spare_parts) are STABLE across runs — they overwrite.
# The event/transaction tables below GROW: each run generates a fresh batch and
# appends it. To keep the appended rows genuinely new AND referentially valid,
# we (a) generate a batch anchored at the CURRENT run window (so timestamps are
# fresh), then (b) offset the primary keys the event/txn tables OWN, plus the
# foreign keys that point BETWEEN those event/txn tables — while leaving foreign
# keys that point at the fixed dimension tables (voyage_id, leg_id, container_id,
# customer_id, *_port_id) UNCHANGED so they still resolve against the base load.
# ---------------------------------------------------------------------------
INCREMENTAL_TABLES: tuple[str, ...] = (
    "bookings",
    "shipments",
    "container_events",
    "port_calls",
    "invoices",
    "invoice_line_items",
)

# Per table: integer PKs the table OWNS + integer FKs that reference ANOTHER
# incremental table (both get offset). Everything else (dimension FKs, business
# strings, timestamps, measures) is left as generated.
_OFFSET_INT_FIELDS: dict[str, tuple[str, ...]] = {
    "bookings": ("booking_id",),
    "shipments": ("shipment_id", "booking_id"),          # booking_id → bookings (incremental)
    "container_events": ("event_id", "shipment_id"),      # shipment_id → shipments (incremental)
    "port_calls": ("port_call_id",),
    "invoices": ("invoice_id", "booking_id"),             # booking_id → bookings (incremental)
    "invoice_line_items": ("line_item_id", "invoice_id"),  # invoice_id → invoices (incremental)
}
# Business-key strings that embed the PK and so must be re-suffixed to stay unique.
_OFFSET_STR_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "bookings": (("booking_no", "BKG{n:08d}"),),
    "invoices": (("invoice_no", "INV-{n:d}"),),  # base uses INV-<year>-<seq>; we re-key by offset id
}


def generate_increment(
    scale: DataScale | str | None = None,
    *,
    id_offset: int,
    today: date | None = None,
) -> GeneratedData:
    """Generate a fresh, appendable batch of ONLY the event/transaction tables.

    ``id_offset`` is added to every owned-PK / intra-event-FK integer (use the
    current max id across runs, e.g. an epoch-derived block or ``MAX(id)`` read
    from Bronze) so appended rows never collide with prior runs. Dimension FKs
    are preserved so the new rows reference the existing base load. ``today``
    anchors the batch's timestamps to the current run window.

    Returns a GeneratedData holding only ``INCREMENTAL_TABLES``.
    """
    full = generate_all(scale, today=today)
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
