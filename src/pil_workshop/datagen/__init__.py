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
