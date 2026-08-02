"""Deterministic generators for reference entities: ports, vessels, routes,
customers. All randomness flows from a single seeded ``numpy`` Generator so
re-runs are byte-identical.

Data is returned as lists of plain dicts (JSON-serializable) so callers can
write them to a Volume as raw Bronze files, or build DataFrames directly.
"""

from __future__ import annotations

from typing import Any

import numpy as np

from ..config import SEED

# ---------------------------------------------------------------------------
# Real major container ports (UN/LOCODE, name, country, region, lat, lon).
# A curated seed list of ~30 anchors; the generator tops up to the requested
# count with plausible secondary ports so foreign keys always resolve.
# ---------------------------------------------------------------------------
_SEED_PORTS: list[tuple[str, str, str, str, float, float]] = [
    ("SGSIN", "Singapore", "Singapore", "Southeast Asia", 1.264, 103.840),
    ("CNSHA", "Shanghai", "China", "East Asia", 31.230, 121.474),
    ("CNNGB", "Ningbo-Zhoushan", "China", "East Asia", 29.868, 121.544),
    ("CNSZX", "Shenzhen", "China", "East Asia", 22.543, 114.058),
    ("CNTAO", "Qingdao", "China", "East Asia", 36.067, 120.383),
    ("HKHKG", "Hong Kong", "Hong Kong", "East Asia", 22.319, 114.169),
    ("KRPUS", "Busan", "South Korea", "East Asia", 35.180, 129.075),
    ("MYPKG", "Port Klang", "Malaysia", "Southeast Asia", 3.000, 101.400),
    ("MYTPP", "Tanjung Pelepas", "Malaysia", "Southeast Asia", 1.363, 103.550),
    ("IDJKT", "Jakarta (Tanjung Priok)", "Indonesia", "Southeast Asia", -6.104, 106.886),
    ("THLCH", "Laem Chabang", "Thailand", "Southeast Asia", 13.083, 100.883),
    ("VNSGN", "Ho Chi Minh City", "Vietnam", "Southeast Asia", 10.762, 106.660),
    ("INNSA", "Nhava Sheva (JNPT)", "India", "South Asia", 18.949, 72.951),
    ("INMAA", "Chennai", "India", "South Asia", 13.083, 80.283),
    ("LKCMB", "Colombo", "Sri Lanka", "South Asia", 6.927, 79.842),
    ("AEJEA", "Jebel Ali", "UAE", "Middle East", 25.010, 55.061),
    ("SAJED", "Jeddah", "Saudi Arabia", "Middle East", 21.485, 39.192),
    ("EGPSD", "Port Said", "Egypt", "Middle East", 31.265, 32.301),
    ("NLRTM", "Rotterdam", "Netherlands", "North Europe", 51.955, 4.140),
    ("DEHAM", "Hamburg", "Germany", "North Europe", 53.545, 9.968),
    ("BEANR", "Antwerp", "Belgium", "North Europe", 51.260, 4.393),
    ("GBFXT", "Felixstowe", "United Kingdom", "North Europe", 51.955, 1.351),
    ("ESVLC", "Valencia", "Spain", "Mediterranean", 39.442, -0.315),
    ("ESALG", "Algeciras", "Spain", "Mediterranean", 36.133, -5.442),
    ("ITGOA", "Genoa", "Italy", "Mediterranean", 44.405, 8.926),
    ("USLAX", "Los Angeles", "United States", "North America West", 33.740, -118.270),
    ("USLGB", "Long Beach", "United States", "North America West", 33.754, -118.216),
    ("USNYC", "New York/New Jersey", "United States", "North America East", 40.669, -74.043),
    ("USSAV", "Savannah", "United States", "North America East", 32.081, -81.096),
    ("BRSSZ", "Santos", "Brazil", "South America", -23.955, -46.333),
    ("ZADUR", "Durban", "South Africa", "Africa", -29.868, 31.020),
    ("AUSYD", "Sydney", "Australia", "Oceania", -33.856, 151.220),
    ("AUMEL", "Melbourne", "Australia", "Oceania", -37.832, 144.921),
    ("JPTYO", "Tokyo", "Japan", "East Asia", 35.617, 139.783),
    ("JPYOK", "Yokohama", "Japan", "East Asia", 35.454, 139.657),
]

# PIL-flavored vessel name stems (PIL uses "Kota" prefix widely).
_VESSEL_STEMS = [
    "Kota",
    "Pacific",
    "Straits",
    "Lion",
    "Merlion",
    "Nanhai",
    "Bengal",
    "Java",
    "Sunda",
    "Malacca",
    "Andaman",
    "Celebes",
    "Coral",
    "Timor",
]
_VESSEL_SUFFIXES = [
    "Ekspres",
    "Nasrat",
    "Nazim",
    "Nabil",
    "Perkasa",
    "Pekarang",
    "Gabung",
    "Halus",
    "Harum",
    "Cabar",
    "Carum",
    "Ratu",
    "Restu",
    "Rukun",
    "Manzanillo",
    "Megah",
    "Lambang",
    "Lestari",
    "Wijaya",
    "Widya",
    "Santos",
    "Setia",
]
_VESSEL_CLASSES = [
    ("Feeder", 1200, 2800),
    ("Panamax", 3000, 5100),
    ("Post-Panamax", 5500, 9000),
    ("Neo-Panamax", 10000, 14000),
    ("ULCV", 15000, 24000),
]
_FUEL_TYPES = ["VLSFO", "VLSFO", "VLSFO", "LNG", "dual-fuel"]


def _rng(seed: int = SEED) -> np.random.Generator:
    return np.random.default_rng(seed)


def gen_ports(n: int) -> list[dict[str, Any]]:
    """Generate ``n`` ports, starting from real anchors, topping up as needed."""
    rng = _rng(SEED + 1)
    ports: list[dict[str, Any]] = []
    for i, (locode, name, country, region, lat, lon) in enumerate(_SEED_PORTS[:n]):
        ports.append(
            {
                "port_id": i + 1,
                "un_locode": locode,
                "port_name": name,
                "country": country,
                "region": region,
                "latitude": round(lat, 4),
                "longitude": round(lon, 4),
                "berth_count": int(rng.integers(3, 40)),
            }
        )
    # Top up with synthetic secondary ports if n exceeds the seed list.
    extra = n - len(ports)
    for j in range(extra):
        base = _SEED_PORTS[j % len(_SEED_PORTS)]
        idx = len(ports) + 1
        ports.append(
            {
                "port_id": idx,
                "un_locode": f"{base[0][:2]}{chr(65 + j % 26)}{chr(65 + (j // 26) % 26)}X",
                "port_name": f"{base[1]} Terminal {j + 2}",
                "country": base[2],
                "region": base[3],
                "latitude": round(base[4] + float(rng.normal(0, 0.4)), 4),
                "longitude": round(base[5] + float(rng.normal(0, 0.4)), 4),
                "berth_count": int(rng.integers(2, 20)),
            }
        )
    return ports


def gen_vessels(n: int) -> list[dict[str, Any]]:
    """Generate ``n`` vessels with IMO numbers, class, capacity, fuel type."""
    rng = _rng(SEED + 2)
    vessels: list[dict[str, Any]] = []
    used_names: set[str] = set()
    for i in range(n):
        cls, cap_lo, cap_hi = _VESSEL_CLASSES[int(rng.integers(0, len(_VESSEL_CLASSES)))]
        # Build a unique PIL-style name.
        for _ in range(20):
            name = (
                f"{_VESSEL_STEMS[int(rng.integers(0, len(_VESSEL_STEMS)))]} "
                f"{_VESSEL_SUFFIXES[int(rng.integers(0, len(_VESSEL_SUFFIXES)))]}"
            )
            if name not in used_names:
                used_names.add(name)
                break
        else:
            name = f"{name} {i}"
        capacity = int(rng.integers(cap_lo, cap_hi))
        vessels.append(
            {
                "vessel_id": i + 1,
                "imo_number": f"IMO{9000000 + i:07d}",
                "vessel_name": name,
                "vessel_class": cls,
                "capacity_teu": capacity,
                "build_year": int(rng.integers(2004, 2024)),
                "fuel_type": _FUEL_TYPES[int(rng.integers(0, len(_FUEL_TYPES)))],
                # Design speed and fuel curve coefficient used later by route opt.
                "service_speed_kn": round(float(rng.uniform(16.0, 23.0)), 1),
            }
        )
    return vessels


# Named liner services with realistic port rotations (indices into ports).
_SERVICE_TEMPLATES: list[tuple[str, str, list[str]]] = [
    ("AR1", "Asia–Red Sea Service", ["SGSIN", "MYPKG", "LKCMB", "AEJEA", "SAJED", "EGPSD"]),
    ("NE2", "Asia–North Europe Loop", ["CNSHA", "CNNGB", "SGSIN", "NLRTM", "DEHAM", "GBFXT"]),
    ("MED3", "Asia–Mediterranean", ["CNSHA", "SGSIN", "INNSA", "ESVLC", "ITGOA", "ESALG"]),
    ("TP4", "Trans-Pacific West", ["CNSHA", "CNNGB", "KRPUS", "USLAX", "USLGB"]),
    ("TP5", "Trans-Pacific East", ["CNSZX", "HKHKG", "USNYC", "USSAV"]),
    ("IA6", "Intra-Asia Express", ["SGSIN", "MYPKG", "THLCH", "VNSGN", "HKHKG", "CNSZX"]),
    ("IA7", "Intra-Asia Feeder", ["SGSIN", "IDJKT", "MYTPP", "MYPKG"]),
    ("SA8", "India–Gulf Service", ["INNSA", "INMAA", "LKCMB", "AEJEA"]),
    ("EA9", "East Asia–Australia", ["CNSHA", "HKHKG", "SGSIN", "AUSYD", "AUMEL"]),
    ("SAf10", "Asia–South Africa", ["SGSIN", "MYPKG", "LKCMB", "ZADUR"]),
    ("SAm11", "Asia–South America", ["CNSHA", "SGSIN", "ZADUR", "BRSSZ"]),
    ("JP12", "Japan–SE Asia", ["JPTYO", "JPYOK", "HKHKG", "SGSIN"]),
]


def gen_routes(n: int, ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate ``n`` liner services with real port rotations."""
    rng = _rng(SEED + 3)
    locode_to_id = {p["un_locode"]: p["port_id"] for p in ports}
    routes: list[dict[str, Any]] = []
    freqs = ["Weekly", "Weekly", "Bi-weekly", "Fortnightly"]
    for i in range(n):
        code, name, rotation = _SERVICE_TEMPLATES[i % len(_SERVICE_TEMPLATES)]
        if i >= len(_SERVICE_TEMPLATES):
            code = f"{code}-{i}"  # keep service_code unique on wrap
        # Only keep ports we actually generated.
        rotation_ids = [locode_to_id[c] for c in rotation if c in locode_to_id]
        if len(rotation_ids) < 2:
            rotation_ids = [p["port_id"] for p in ports[:4]]
        routes.append(
            {
                "route_id": i + 1,
                "service_code": code,
                "route_name": name,
                "port_rotation": rotation_ids,  # ordered list of port_id
                "port_rotation_locodes": [ports[pid - 1]["un_locode"] for pid in rotation_ids],
                "frequency": freqs[int(rng.integers(0, len(freqs)))],
                "leg_count": len(rotation_ids),
            }
        )
    return routes


_INDUSTRIES = [
    "Electronics",
    "Automotive",
    "Retail & Apparel",
    "Chemicals",
    "Food & Beverage",
    "Machinery",
    "Furniture",
    "Pharmaceuticals",
    "Commodities",
    "Construction",
]
_CUSTOMER_TYPES = ["Shipper (BCO)", "Consignee", "Freight Forwarder", "NVOCC"]
_CREDIT_TERMS = ["Prepaid", "Net 15", "Net 30", "Net 45", "Net 60"]


def gen_customers(n: int, ports: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Generate ``n`` customers with type, industry, country, credit terms."""
    rng = _rng(SEED + 4)
    countries = sorted({p["country"] for p in ports})
    customers: list[dict[str, Any]] = []
    for i in range(n):
        cust_type = _CUSTOMER_TYPES[int(rng.integers(0, len(_CUSTOMER_TYPES)))]
        industry = _INDUSTRIES[int(rng.integers(0, len(_INDUSTRIES)))]
        customers.append(
            {
                "customer_id": i + 1,
                "customer_name": f"{industry.split()[0]} {cust_type.split()[0]} "
                f"{['Global', 'Intl', 'Trading', 'Logistics', 'Group'][i % 5]} "
                f"{i + 1:04d}",
                "customer_type": cust_type,
                "industry": industry,
                "country": countries[int(rng.integers(0, len(countries)))],
                "credit_terms": _CREDIT_TERMS[int(rng.integers(0, len(_CREDIT_TERMS)))],
                "credit_limit_usd": int(rng.integers(50, 2000)) * 1000,
            }
        )
    return customers
