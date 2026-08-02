"""Deterministic generator for spare-parts inventory + daily consumption.

Feeds the Phase 5 forecasting module. Consumption is intentionally a mix of:

* **smooth** movers (steady demand) — where ETS/LightGBM shine, and
* **intermittent** movers (many zero-days, occasional spikes) — where
  Croston/TSB is the right default, per the brainstorm write-up.

Depots correspond to 8 hub ports so the data ties back to the network.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import numpy as np

from ..config import DataScale, history_window

_PART_CATEGORIES = [
    ("Engine Spares", 5, 4000),
    ("Reefer Parts", 8, 2500),
    ("Lashing Gear", 2, 600),
    ("Electrical", 3, 1500),
    ("Hydraulics", 6, 3200),
    ("Deck Equipment", 4, 1800),
    ("Safety Equipment", 2, 900),
]
_DEPOTS = ["SGSIN", "CNSHA", "AEJEA", "NLRTM", "USLAX", "INNSA", "HKHKG", "ZADUR"]


def _rng(salt: int) -> np.random.Generator:
    return np.random.default_rng(8008 + salt)


def gen_spare_parts(scale: DataScale) -> list[dict[str, Any]]:
    """Generate the spare-parts SKU master across categories and depots."""
    rng = _rng(0)
    parts: list[dict[str, Any]] = []
    for i in range(scale.spare_parts_skus):
        cat, price_lo, price_hi = _PART_CATEGORIES[int(rng.integers(0, len(_PART_CATEGORIES)))]
        # ~45% of SKUs are intermittent movers.
        pattern = "intermittent" if rng.random() < 0.45 else "smooth"
        parts.append(
            {
                "sku_id": i + 1,
                "sku_code": f"SP-{cat[:3].upper()}-{i + 1:05d}",
                "category": cat,
                "depot": _DEPOTS[int(rng.integers(0, len(_DEPOTS)))],
                "unit_cost_usd": round(float(rng.uniform(price_lo, price_hi)), 2),
                "lead_time_days": int(rng.integers(7, 90)),
                "demand_pattern": pattern,
                "reorder_point": int(rng.integers(2, 40)),
            }
        )
    return parts


def gen_consumption(
    scale: DataScale,
    parts: list[dict[str, Any]],
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Generate daily consumption transactions per SKU over the history window.

    To keep the demo scale tractable, consumption is generated for a capped
    subset of SKUs across ``inventory_days`` days; the full run covers all SKUs.
    """
    rng = _rng(1)
    start, end = history_window(today)
    n_days = min(scale.inventory_days, (end - start).days)
    day0 = end - timedelta(days=n_days - 1)

    # Cap the SKU count actually simulated day-by-day for the demo scale.
    max_series = 400 if scale.name == "demo" else len(parts)
    sim_parts = parts[:max_series]

    rows: list[dict[str, Any]] = []
    for p in sim_parts:
        pattern = p["demand_pattern"]
        base = (
            float(rng.uniform(0.4, 4.0)) if pattern == "smooth" else float(rng.uniform(0.05, 0.6))
        )
        # Seasonality + weekly pattern coefficients.
        season_amp = float(rng.uniform(0.1, 0.5))
        for d in range(n_days):
            cur = day0 + timedelta(days=d)
            doy = cur.timetuple().tm_yday
            seasonal = 1.0 + season_amp * np.sin(2 * np.pi * doy / 365.0)
            weekly = 0.7 if cur.weekday() >= 5 else 1.0
            lam = base * seasonal * weekly
            if pattern == "intermittent":
                # Many zeros; occasional demand.
                qty = int(rng.poisson(lam)) if rng.random() < 0.35 else 0
            else:
                qty = int(rng.poisson(lam))
            if qty <= 0:
                continue
            rows.append(
                {
                    "sku_id": p["sku_id"],
                    "sku_code": p["sku_code"],
                    "depot": p["depot"],
                    "txn_date": cur.isoformat(),
                    "quantity": qty,
                    "category": p["category"],
                }
            )
    return rows
