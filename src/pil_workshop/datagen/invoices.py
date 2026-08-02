"""Deterministic generator for structured freight invoices + line items.

These are the *ground-truth structured* invoices (``silver.invoices`` /
``silver.invoice_line_items``). Phase 3 separately renders ~200 of them as PDFs
and re-extracts them with ``ai_parse_document`` / ``ai_query`` so the workshop
can reconcile extracted-vs-actual and surface exceptions.

Financials are internally consistent: subtotal = sum(line items),
tax = subtotal * rate, total = subtotal + tax (except for the deliberate ~10%
anomaly rows, which are flagged in ground truth so the exception logic has
something real to catch).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import numpy as np

from ..config import DataScale, history_window

# Charge types that appear as invoice line items.
_CHARGE_TYPES = [
    ("Ocean Freight", 700, 2500),
    ("Terminal Handling (THC)", 120, 450),
    ("Bunker Adjustment (BAF)", 60, 300),
    ("Documentation Fee", 25, 75),
    ("Demurrage", 0, 1200),
    ("Detention", 0, 900),
    ("Container Cleaning", 30, 120),
    ("Customs Clearance", 80, 260),
]
_CURRENCIES = ["USD", "USD", "USD", "SGD", "EUR", "CNY"]
_FX_TO_USD = {"USD": 1.0, "SGD": 0.74, "EUR": 1.08, "CNY": 0.14}
_STATUSES = ["Paid", "Paid", "Paid", "Open", "Overdue", "Disputed"]


def _rng(salt: int) -> np.random.Generator:
    return np.random.default_rng(7007 + salt)


def gen_invoices(
    scale: DataScale,
    bookings: list[dict[str, Any]],
    customers: list[dict[str, Any]],
    today: date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Generate structured invoices + line items tied to non-cancelled bookings.

    Returns ``(invoices, line_items)``. DSO (days sales outstanding) is
    engineered into the 30–75 day band via issue/paid date spacing.
    """
    rng = _rng(0)
    _, end = history_window(today)
    active_bookings = [b for b in bookings if not b.get("is_cancelled")]
    if not active_bookings:
        return [], []

    invoices: list[dict[str, Any]] = []
    line_items: list[dict[str, Any]] = []
    inv_id = 0
    li_id = 0
    n_target = min(scale.invoices, len(active_bookings))
    dup_pool: list[str] = []  # for injecting duplicate invoice numbers

    for i in range(n_target):
        booking = active_bookings[i]
        customer = next(
            (c for c in customers if c["customer_id"] == booking["customer_id"]),
            customers[0],
        )
        inv_id += 1
        currency = _CURRENCIES[int(rng.integers(0, len(_CURRENCIES)))]
        issue_date = datetime.fromisoformat(booking["booking_ts"]).date() + timedelta(
            days=int(rng.integers(1, 20))
        )
        # Build 2–5 line items.
        n_lines = int(rng.integers(2, 6))
        chosen = rng.choice(len(_CHARGE_TYPES), size=n_lines, replace=False)
        subtotal = 0.0
        this_lines: list[dict[str, Any]] = []
        for ci in chosen:
            name, lo, hi = _CHARGE_TYPES[int(ci)]
            amount = float(rng.uniform(lo, hi)) * booking["container_count"]
            if amount <= 0:
                continue
            li_id += 1
            subtotal += amount
            this_lines.append(
                {
                    "line_item_id": li_id,
                    "invoice_id": inv_id,
                    "charge_type": name,
                    "quantity": booking["container_count"],
                    "unit_amount": round(amount / max(booking["container_count"], 1), 2),
                    "amount": round(amount, 2),
                    "currency": currency,
                }
            )

        tax_rate = 0.0 if currency == "USD" else 0.07
        tax = subtotal * tax_rate
        total = subtotal + tax

        # Deliberate ~10% anomalies: wrong total, missing PO, or duplicate no.
        anomaly = None
        is_anomaly = rng.random() < 0.10
        po_number: str | None = f"PO{booking['booking_id']:08d}"
        invoice_no = f"INV-{issue_date.year}-{inv_id:07d}"
        if is_anomaly:
            kind = rng.integers(0, 3)
            if kind == 0:
                total = round(total * float(rng.uniform(1.05, 1.4)), 2)  # wrong total
                anomaly = "total_mismatch"
            elif kind == 1:
                po_number = None  # missing PO
                anomaly = "missing_po"
            elif dup_pool:
                invoice_no = dup_pool[int(rng.integers(0, len(dup_pool)))]
                anomaly = "duplicate_no"
        else:
            dup_pool.append(invoice_no)

        # Payment / DSO.
        status = _STATUSES[int(rng.integers(0, len(_STATUSES)))]
        paid_date: date | None = None
        if status == "Paid":
            dso = int(rng.integers(25, 78))
            paid_date = issue_date + timedelta(days=dso)
            if paid_date > end:
                paid_date = None
                status = "Open"

        invoices.append(
            {
                "invoice_id": inv_id,
                "invoice_no": invoice_no,
                "booking_id": booking["booking_id"],
                "customer_id": customer["customer_id"],
                "po_number": po_number,
                "issue_date": issue_date.isoformat(),
                "due_date": (issue_date + timedelta(days=30)).isoformat(),
                "paid_date": paid_date.isoformat() if paid_date else None,
                "currency": currency,
                "fx_to_usd": _FX_TO_USD[currency],
                "subtotal": round(subtotal, 2),
                "tax": round(tax, 2),
                "total": round(total, 2),
                "total_usd": round(total * _FX_TO_USD[currency], 2),
                "status": status,
                "is_disputed": status == "Disputed",
                # Ground-truth anomaly label (not shown to extraction; used for eval).
                "gt_anomaly": anomaly,
            }
        )
        for li in this_lines:
            line_items.append(li)
    return invoices, line_items
