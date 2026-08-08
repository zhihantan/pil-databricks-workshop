"""Deterministic generators for transactional/operational data: voyages and
legs, containers, bookings/shipments, container events, port calls, and
invoices with line items.

KPIs are engineered *by construction* so the smoke tests in ``04_build_gold``
land inside :data:`pil_workshop.config.KPI_RANGES`. For example, on-time status
is drawn from a Bernoulli tuned to the target reliability band and the
timestamps are then derived from that status — rather than hoping the tail of
some delay distribution happens to fall in range.

Every function takes a scale spec and returns lists of JSON-serializable dicts
(dates rendered ISO-8601). Deliberate Bronze messiness (nulls, dupes, mixed
date formats, bad codes, negative dwell) is injected by
:func:`inject_bronze_messiness` so Silver has real cleaning work.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from ..config import DataScale, history_window
from .iso6346 import container_number

# Container types with TEU factor and reefer flag.
_CONTAINER_TYPES = [
    ("20GP", 1.0, False),
    ("40GP", 2.0, False),
    ("40HC", 2.0, False),
    ("20RF", 1.0, True),
    ("40RF", 2.0, True),
    ("20OT", 1.0, False),
    ("40FR", 2.0, False),
]
_CONTAINER_TYPE_WEIGHTS = [0.34, 0.27, 0.20, 0.05, 0.06, 0.04, 0.04]
_OWNER_CODES = ["PCI", "PIL", "KOT", "PCL"]  # PIL BIC prefixes (synthetic-ish)

_COMMODITIES = [
    "Electronics",
    "Auto Parts",
    "Apparel",
    "Furniture",
    "Machinery",
    "Chemicals",
    "Foodstuffs",
    "Plastics",
    "Paper",
    "Steel Products",
    "Refrigerated Food",
    "Pharmaceuticals",
    "Building Materials",
]
_CONTAINER_STATUSES = ["At Origin", "In Transit", "At Destination", "Empty Returned"]


def _rng(base: int, salt: int = 0) -> np.random.Generator:
    return np.random.default_rng(base + salt)


def _iso(dt: datetime) -> str:
    return dt.replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Voyages + legs
# ---------------------------------------------------------------------------
def gen_voyages_and_legs(
    scale: DataScale,
    vessels: list[dict[str, Any]],
    routes: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    today: date | None = None,
    window_start: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate voyages and their leg-level ETD/ETA/ATD/ATA with realistic delays.

    Reliability is engineered: each leg arrival is "on time" with probability
    ``p_on_time`` (~0.74) so schedule-reliability lands in the 60–85% band.

    ``window_start`` (optional) narrows where voyage start dates (``etd0``) are
    sampled from: when given, departures fall in ``[window_start, end]`` instead
    of the full ~24-month history. The incremental append path uses this so a
    fresh slice is dated in the RECENT window (visible daily growth) rather than
    scattered across the whole history. ``None`` = full-window (the base load).
    """
    rng = _rng(1001)
    start, end = history_window(today)
    if window_start is not None:
        # Clamp into the history window; the recent slice starts no earlier than
        # the base window's start and no later than a day before the end.
        start = max(start, min(window_start, end - timedelta(days=1)))
    port_by_id = {p["port_id"]: p for p in ports}

    voyages: list[dict[str, Any]] = []
    legs: list[dict[str, Any]] = []
    voyage_id = 0
    leg_id = 0
    p_on_time = 0.74

    n_voyages = scale.voyages
    while voyage_id < n_voyages:
        route = routes[int(rng.integers(0, len(routes)))]
        vessel = vessels[int(rng.integers(0, len(vessels)))]
        rotation = route["port_rotation"]
        if len(rotation) < 2:
            continue
        voyage_id += 1
        # Random start somewhere in the window. Reserve a tail so a multi-leg
        # voyage can finish inside the window; for a NARROW recent window (the
        # incremental slice) the reserve shrinks to half the span so departures
        # still spread across it instead of piling on the first day. For the full
        # base window (span ~730d) this stays 40, so the base load is unchanged.
        span_days = (end - start).days
        reserve = min(40, max(0, span_days // 2))
        etd0 = datetime.combine(
            start + timedelta(days=int(rng.integers(0, max(1, span_days - reserve)))),
            datetime.min.time(),
        ) + timedelta(hours=int(rng.integers(0, 24)))

        cursor = etd0
        total_fuel = 0.0
        leg_count = len(rotation) - 1
        # Per-voyage base load factor (70-95% band); legs vary slightly around it.
        # Utilization is a manifest quantity, so it is engineered here rather
        # than reconstructed by counting the sampled bookings/shipments.
        base_load_factor = float(rng.uniform(0.72, 0.93))
        for leg_no in range(leg_count):
            origin = port_by_id[rotation[leg_no]]
            dest = port_by_id[rotation[leg_no + 1]]
            # Transit time proportional to great-circle distance / speed.
            dist_nm = _haversine_nm(origin, dest)
            speed = vessel["service_speed_kn"]
            sea_hours = dist_nm / max(speed, 1e-6)
            etd = cursor
            eta = etd + timedelta(hours=sea_hours)

            on_time = rng.random() < p_on_time
            if on_time:
                delay_h = float(rng.uniform(-6, 20))  # within 24h → reliable
            else:
                delay_h = float(rng.uniform(28, 96))  # clearly late
            ata = eta + timedelta(hours=delay_h)
            # Departure delay is usually smaller.
            atd = etd + timedelta(hours=float(rng.uniform(-2, 10)))

            # Fuel for the leg (mt): cube-law in speed, scaled by capacity.
            # Coefficient calibrated so a ~8k-TEU vessel at 20 kn burns
            # ~160 mt/day — in line with real large-containership figures —
            # which puts fuel efficiency in the 0.02-0.25 mt/1k-TEU-nm band.
            fuel_leg = (speed**3) * 2.0e-2 * sea_hours / 24.0 * (vessel["capacity_teu"] / 8000.0)
            if vessel["fuel_type"] == "LNG":
                fuel_leg *= 0.82
            elif vessel["fuel_type"] == "dual-fuel":
                fuel_leg *= 0.90
            total_fuel += fuel_leg

            # Manifest load for this leg: capacity * per-leg load factor.
            capacity = vessel["capacity_teu"]
            leg_load_factor = min(0.99, max(0.55, base_load_factor + float(rng.normal(0, 0.05))))
            loaded_teu = int(round(capacity * leg_load_factor))

            leg_id += 1
            legs.append(
                {
                    "leg_id": leg_id,
                    "voyage_id": voyage_id,
                    "leg_sequence": leg_no + 1,
                    "origin_port_id": origin["port_id"],
                    "dest_port_id": dest["port_id"],
                    "distance_nm": round(dist_nm, 1),
                    "etd": _iso(etd),
                    "eta": _iso(eta),
                    "atd": _iso(atd),
                    "ata": _iso(ata),
                    "arrival_delay_hrs": round((ata - eta).total_seconds() / 3600.0, 2),
                    "on_time": bool(on_time),
                    "fuel_consumed_mt": round(fuel_leg, 2),
                    "capacity_teu": capacity,
                    "loaded_teu": loaded_teu,
                    "load_factor": round(leg_load_factor, 4),
                }
            )
            # Port stay before next leg.
            cursor = ata + timedelta(hours=float(rng.uniform(10, 40)))

        voyages.append(
            {
                "voyage_id": voyage_id,
                "voyage_no": f"{route['service_code']}-{voyage_id:05d}",
                "vessel_id": vessel["vessel_id"],
                "route_id": route["route_id"],
                "departure_date": etd0.date().isoformat(),
                "leg_count": leg_count,
                "total_fuel_consumed_mt": round(total_fuel, 2),
                "status": "Completed" if etd0.date() < end - timedelta(days=30) else "Active",
            }
        )
    return voyages, legs


def _haversine_nm(a: dict[str, Any], b: dict[str, Any]) -> float:
    """Great-circle distance between two ports in nautical miles."""
    r_nm = 3440.065
    lat1, lon1, lat2, lon2 = map(
        np.radians, [a["latitude"], a["longitude"], b["latitude"], b["longitude"]]
    )
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = np.sin(dlat / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    d = 2 * r_nm * np.arcsin(np.sqrt(h))
    # Floor so co-located synthetic ports still yield a positive transit.
    return float(max(d, 120.0))


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------
def gen_containers(scale: DataScale) -> list[dict[str, Any]]:
    """Generate the container fleet with valid ISO 6346 numbers."""
    rng = _rng(2002)
    containers: list[dict[str, Any]] = []
    for i in range(scale.containers):
        owner = _OWNER_CODES[int(rng.integers(0, len(_OWNER_CODES)))]
        ctype_idx = int(rng.choice(len(_CONTAINER_TYPES), p=_CONTAINER_TYPE_WEIGHTS))
        ctype, teu, is_reefer = _CONTAINER_TYPES[ctype_idx]
        containers.append(
            {
                "container_id": i + 1,
                "container_no": container_number(owner, "U", 100000 + i),
                "container_type": ctype,
                "teu_factor": teu,
                "is_reefer": is_reefer,
                "condition": rng.choice(["Good", "Good", "Good", "Fair", "Damaged"]).item(),
                "current_status": _CONTAINER_STATUSES[
                    int(rng.integers(0, len(_CONTAINER_STATUSES)))
                ],
                "year_built": int(rng.integers(2010, 2025)),
            }
        )
    return containers


# ---------------------------------------------------------------------------
# Bookings / shipments (+ containers on booking)
# ---------------------------------------------------------------------------
def gen_bookings_and_shipments(
    scale: DataScale,
    voyages: list[dict[str, Any]],
    legs: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    containers: list[dict[str, Any]],
    ports: list[dict[str, Any]],
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate bookings and per-container shipment rows tied to voyage legs.

    Vessel utilization is engineered per-voyage to land in the 70–95% band by
    sizing the number of containers assigned to each voyage against capacity.
    """
    rng = _rng(3003)
    legs_by_voyage: dict[int, list[dict[str, Any]]] = {}
    for leg in legs:
        legs_by_voyage.setdefault(leg["voyage_id"], []).append(leg)

    bookings: list[dict[str, Any]] = []
    shipments: list[dict[str, Any]] = []
    booking_id = 0
    shipment_id = 0
    n_target = scale.bookings
    cancel_rate = 0.06  # → booking cancellation rate ~2-12% band

    # Distribute bookings across voyages weighted by leg count.
    voyage_ids = [v["voyage_id"] for v in voyages if v["voyage_id"] in legs_by_voyage]
    if not voyage_ids:
        return bookings, shipments

    per_voyage = max(1, n_target // len(voyage_ids))
    for vid in voyage_ids:
        vlegs = sorted(legs_by_voyage[vid], key=lambda x: x["leg_sequence"])
        for _ in range(per_voyage):
            if booking_id >= n_target:
                break
            booking_id += 1
            leg = vlegs[int(rng.integers(0, len(vlegs)))]
            customer = customers[int(rng.integers(0, len(customers)))]
            n_containers = int(rng.integers(1, 4))
            etd = datetime.fromisoformat(leg["etd"])
            booked_at = etd - timedelta(days=float(rng.uniform(5, 30)))
            is_cancelled = rng.random() < cancel_rate
            status = (
                "Cancelled"
                if is_cancelled
                else rng.choice(["Confirmed", "Confirmed", "Completed"]).item()
            )

            freight_rate = float(rng.uniform(700, 2500))
            bookings.append(
                {
                    "booking_id": booking_id,
                    "booking_no": f"BKG{booking_id:08d}",
                    "customer_id": customer["customer_id"],
                    "voyage_id": vid,
                    "leg_id": leg["leg_id"],
                    "pol_port_id": leg["origin_port_id"],
                    "pod_port_id": leg["dest_port_id"],
                    "commodity": _COMMODITIES[int(rng.integers(0, len(_COMMODITIES)))],
                    "container_count": n_containers,
                    "freight_rate_usd": round(freight_rate, 2),
                    "booking_ts": _iso(booked_at),
                    "status": status,
                    "is_cancelled": is_cancelled,
                }
            )
            if is_cancelled:
                continue
            for _c in range(n_containers):
                shipment_id += 1
                cont = containers[int(rng.integers(0, len(containers)))]
                gate_in = etd - timedelta(hours=float(rng.uniform(24, 96)))
                load = etd + timedelta(hours=float(rng.uniform(0, 8)))
                discharge = datetime.fromisoformat(leg["ata"]) + timedelta(
                    hours=float(rng.uniform(2, 18))
                )
                gate_out = discharge + timedelta(hours=float(rng.uniform(12, 120)))
                shipments.append(
                    {
                        "shipment_id": shipment_id,
                        "booking_id": booking_id,
                        "container_id": cont["container_id"],
                        "container_no": cont["container_no"],
                        "container_type": cont["container_type"],
                        "teu": cont["teu_factor"],
                        "is_reefer": cont["is_reefer"],
                        "pol_port_id": leg["origin_port_id"],
                        "pod_port_id": leg["dest_port_id"],
                        "gate_in_ts": _iso(gate_in),
                        "load_ts": _iso(load),
                        "discharge_ts": _iso(discharge),
                        "gate_out_ts": _iso(gate_out),
                        "dwell_hrs": round((gate_out - discharge).total_seconds() / 3600.0, 2),
                    }
                )
        if booking_id >= n_target:
            break
    return bookings, shipments


# ---------------------------------------------------------------------------
# Container events (IoT-style stream)
# ---------------------------------------------------------------------------
def gen_container_events(
    scale: DataScale,
    shipments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate an event stream from shipment lifecycle timestamps.

    Emits gate-in / load / discharge / gate-out plus occasional customs-hold
    and damage events, capped at the scale's ``container_events`` target.
    """
    rng = _rng(4004)
    events: list[dict[str, Any]] = []
    event_id = 0
    cap = scale.container_events
    for sh in shipments:
        base_events = [
            ("GATE_IN", sh["gate_in_ts"]),
            ("LOAD", sh["load_ts"]),
            ("DISCHARGE", sh["discharge_ts"]),
            ("GATE_OUT", sh["gate_out_ts"]),
        ]
        for etype, ts in base_events:
            event_id += 1
            events.append(
                {
                    "event_id": event_id,
                    "shipment_id": sh["shipment_id"],
                    "container_no": sh["container_no"],
                    "event_type": etype,
                    "event_ts": ts,
                    "damage_flag": False,
                    "dwell_hrs": sh["dwell_hrs"] if etype == "GATE_OUT" else None,
                }
            )
            if event_id >= cap:
                return events
        # ~4% customs hold, ~3% damage.
        if rng.random() < 0.04:
            event_id += 1
            events.append(
                {
                    "event_id": event_id,
                    "shipment_id": sh["shipment_id"],
                    "container_no": sh["container_no"],
                    "event_type": "CUSTOMS_HOLD",
                    "event_ts": sh["discharge_ts"],
                    "damage_flag": False,
                    "dwell_hrs": None,
                }
            )
            if event_id >= cap:
                return events
        if rng.random() < 0.03:
            event_id += 1
            events.append(
                {
                    "event_id": event_id,
                    "shipment_id": sh["shipment_id"],
                    "container_no": sh["container_no"],
                    "event_type": "DAMAGE",
                    "event_ts": sh["discharge_ts"],
                    "damage_flag": True,
                    "dwell_hrs": None,
                }
            )
            if event_id >= cap:
                return events
    return events


# ---------------------------------------------------------------------------
# Port calls
# ---------------------------------------------------------------------------
def gen_port_calls(
    scale: DataScale,
    voyages: list[dict[str, Any]],
    legs: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Generate berth-level port calls with waiting and turnaround times.

    Turnaround engineered into the 12–60h band, dwell/waiting into plausible
    ranges.
    """
    rng = _rng(5005)
    calls: list[dict[str, Any]] = []
    call_id = 0
    cap = scale.port_calls
    for leg in legs:
        if call_id >= cap:
            break
        call_id += 1
        arrival = datetime.fromisoformat(leg["ata"])
        waiting = float(rng.uniform(1, 30))
        turnaround = float(rng.uniform(12, 58))
        berth = arrival + timedelta(hours=waiting)
        departure = berth + timedelta(hours=turnaround)
        crane_moves = int(rng.integers(400, 3500))
        calls.append(
            {
                "port_call_id": call_id,
                "voyage_id": leg["voyage_id"],
                "port_id": leg["dest_port_id"],
                "arrival_ts": _iso(arrival),
                "berth_ts": _iso(berth),
                "departure_ts": _iso(departure),
                "waiting_time_hrs": round(waiting, 2),
                "turnaround_hrs": round(turnaround, 2),
                "crane_moves": crane_moves,
            }
        )
    return calls
