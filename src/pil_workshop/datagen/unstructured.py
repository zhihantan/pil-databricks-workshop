"""Generators for unstructured Phase-3 data: freight-invoice PDFs (reportlab)
and labeled container images (Pillow).

Both are deterministic given ``seed`` and take an output directory (a Volume
path inside a notebook). They return ground-truth records so the workshop can
reconcile extracted-vs-actual (invoices) and score classification accuracy
(images). Imports of reportlab/Pillow are lazy so importing the ``datagen``
package off-platform doesn't require them.
"""

from __future__ import annotations

import os
import random
from datetime import date, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Invoice PDFs
# ---------------------------------------------------------------------------
# An invoice is an IMMUTABLE historical document — its number and issue date are
# assigned once and never change. So invoice generation is anchored to a FIXED
# reference date, NOT date.today(). This keeps every run byte-for-byte stable:
# file ``invoice_{i}.pdf`` always renders the same invoice_no / issue_date /
# totals. (The structured medallion data still anchors to "today" for dashboard
# freshness — see config.history_window — but documents must not drift, or a
# once-seeded review queue falls out of sync with the regenerated PDFs.)
_INVOICE_ANCHOR = date(2026, 6, 30)
_TEMPLATES = ["classic", "modern", "compact", "banded"]
_CURRENCIES = ["USD", "SGD", "EUR", "CNY"]
_CHARGES = [
    ("Ocean Freight", 700, 2500),
    ("Terminal Handling (THC)", 120, 450),
    ("Bunker Adjustment (BAF)", 60, 300),
    ("Documentation Fee", 25, 75),
    ("Demurrage", 0, 1200),
    ("Detention", 0, 900),
]
_CUSTOMER_NAMES = [
    "Meridian Electronics Pte Ltd", "Auburn Auto Parts Co.",
    "Northwind Apparel Group", "Kirin Foods Trading",
    "Zenith Machinery Ltd", "Delta Chemicals Intl",
    "Harbor Furniture Works", "Summit Pharma Logistics",
]


def generate_invoice_pdfs(
    out_dir: str, n: int, seed: int = 42
) -> list[dict[str, Any]]:
    """Render ``n`` freight-invoice PDFs into ``out_dir``; return ground truth.

    ~10% of invoices carry a deliberate anomaly (wrong total, missing PO, or a
    duplicate invoice number) recorded in the returned ground-truth dicts.
    """
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas

    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    ground_truth: list[dict[str, Any]] = []
    seen_numbers: list[str] = []

    navy = colors.HexColor("#0B1F3A")
    teal = colors.HexColor("#0E7C86")

    for i in range(n):
        template = _TEMPLATES[i % len(_TEMPLATES)]
        currency = rng.choice(_CURRENCIES)
        # Anchored to a FIXED reference (not date.today()) so the invoice_no
        # (which embeds issue.year) and the document are stable across runs.
        issue = _INVOICE_ANCHOR - timedelta(days=rng.randint(10, 700))
        customer = rng.choice(_CUSTOMER_NAMES)
        n_lines = rng.randint(2, 5)
        picks = rng.sample(_CHARGES, n_lines)
        containers = rng.randint(1, 4)

        lines = []
        subtotal = 0.0
        for name, lo, hi in picks:
            amt = round(rng.uniform(lo, hi) * containers, 2)
            if amt <= 0:
                continue
            subtotal += amt
            lines.append((name, containers, round(amt / containers, 2), amt))

        tax_rate = 0.0 if currency == "USD" else 0.07
        tax = round(subtotal * tax_rate, 2)
        total = round(subtotal + tax, 2)

        invoice_no = f"INV-{issue.year}-{100000 + i}"
        po_number: str | None = f"PO{200000 + i}"
        anomaly = None
        if rng.random() < 0.10:
            kind = rng.randint(0, 2)
            if kind == 0:
                total = round(total * rng.uniform(1.05, 1.4), 2)  # wrong total
                anomaly = "total_mismatch"
            elif kind == 1:
                po_number = None
                anomaly = "missing_po"
            elif seen_numbers:
                invoice_no = rng.choice(seen_numbers)
                anomaly = "duplicate_no"
        else:
            seen_numbers.append(invoice_no)

        fname = f"invoice_{i:04d}.pdf"
        path = os.path.join(out_dir, fname)
        _render_invoice_pdf(
            canvas, A4, mm, navy, teal, path, template,
            invoice_no, po_number, customer, issue, currency, lines,
            subtotal, tax, total,
        )
        ground_truth.append({
            "file_name": fname,
            "invoice_no": invoice_no,
            "po_number": po_number,
            "customer": customer,
            "issue_date": issue.isoformat(),
            "currency": currency,
            "subtotal": round(subtotal, 2),
            "tax": tax,
            "total": total,
            "n_line_items": len(lines),
            "template": template,
            "gt_anomaly": anomaly,
        })
    return ground_truth


def _render_invoice_pdf(
    canvas_mod, pagesize, mm, navy, teal, path, template, invoice_no, po_number,
    customer, issue, currency, lines, subtotal, tax, total,
) -> None:
    """Draw one invoice PDF with a template-specific look."""
    width, height = pagesize
    c = canvas_mod.Canvas(path, pagesize=pagesize)

    # Header band varies by template.
    if template in ("modern", "banded"):
        c.setFillColor(navy)
        c.rect(0, height - 32 * mm, width, 32 * mm, fill=1, stroke=0)
        c.setFillColor(teal if template == "modern" else navy)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 20)
        c.drawString(18 * mm, height - 20 * mm, "PIL — Pacific International Lines")
        c.setFont("Helvetica", 10)
        c.drawString(18 * mm, height - 27 * mm, "Freight Invoice")
    else:
        c.setFillColor(navy)
        c.setFont("Helvetica-Bold", 18)
        c.drawString(18 * mm, height - 20 * mm, "Pacific International Lines")
        c.setStrokeColor(teal)
        c.setLineWidth(2)
        c.line(18 * mm, height - 24 * mm, width - 18 * mm, height - 24 * mm)

    c.setFillColor(navy)
    y = height - 45 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(18 * mm, y, f"Invoice No: {invoice_no}")
    c.setFont("Helvetica", 10)
    c.drawString(18 * mm, y - 6 * mm, f"PO Number: {po_number or '—'}")
    c.drawString(18 * mm, y - 12 * mm, f"Issue Date: {issue.isoformat()}")
    c.drawString(18 * mm, y - 18 * mm, f"Currency: {currency}")
    c.drawRightString(width - 18 * mm, y, f"Bill To: {customer}")

    # Line-item table.
    ty = y - 32 * mm
    c.setFont("Helvetica-Bold", 10)
    c.setFillColor(teal)
    c.drawString(18 * mm, ty, "Description")
    c.drawString(110 * mm, ty, "Qty")
    c.drawString(130 * mm, ty, "Unit")
    c.drawRightString(width - 18 * mm, ty, "Amount")
    c.setFillColor(navy)
    c.setFont("Helvetica", 10)
    ty -= 6 * mm
    for name, qty, unit, amt in lines:
        c.drawString(18 * mm, ty, name)
        c.drawString(110 * mm, ty, str(qty))
        c.drawString(130 * mm, ty, f"{unit:,.2f}")
        c.drawRightString(width - 18 * mm, ty, f"{amt:,.2f}")
        ty -= 6 * mm

    ty -= 4 * mm
    c.setStrokeColor(teal)
    c.line(110 * mm, ty, width - 18 * mm, ty)
    ty -= 6 * mm
    c.drawString(110 * mm, ty, "Subtotal")
    c.drawRightString(width - 18 * mm, ty, f"{subtotal:,.2f} {currency}")
    ty -= 6 * mm
    c.drawString(110 * mm, ty, "Tax")
    c.drawRightString(width - 18 * mm, ty, f"{tax:,.2f} {currency}")
    ty -= 6 * mm
    c.setFont("Helvetica-Bold", 11)
    c.drawString(110 * mm, ty, "TOTAL")
    c.drawRightString(width - 18 * mm, ty, f"{total:,.2f} {currency}")

    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(navy)
    c.drawString(18 * mm, 15 * mm,
                 "Payment terms: Net 30. Thank you for shipping with PIL.")
    c.showPage()
    c.save()


# ---------------------------------------------------------------------------
# Container images
# ---------------------------------------------------------------------------
_DAMAGE_CLASSES = ["none", "minor", "major"]
_CONTAINER_COLORS = [
    (196, 78, 62), (46, 92, 122), (74, 122, 74), (196, 152, 62), (120, 120, 128),
]


def generate_container_images(
    out_dir: str, n: int, seed: int = 42
) -> list[dict[str, Any]]:
    """Draw ``n`` labeled container images into ``out_dir``; return ground truth.

    Each image shows a container with its ISO number; damage overlays (dents,
    rust, door misalignment) are drawn for the ``minor``/``major`` classes. The
    returned records carry the ground-truth damage label for accuracy scoring.
    """
    from PIL import Image, ImageDraw, ImageFont

    os.makedirs(out_dir, exist_ok=True)
    rng = random.Random(seed)
    gt: list[dict[str, Any]] = []

    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
        small = ImageFont.truetype("DejaVuSans.ttf", 16)
    except Exception:  # noqa: BLE001 - fall back to default bitmap font
        font = ImageFont.load_default()
        small = ImageFont.load_default()

    W, H = 640, 420
    for i in range(n):
        # ~45% none, ~35% minor, ~20% major.
        r = rng.random()
        damage = "none" if r < 0.45 else ("minor" if r < 0.80 else "major")
        body = rng.choice(_CONTAINER_COLORS)
        img = Image.new("RGB", (W, H), (222, 228, 233))
        d = ImageDraw.Draw(img)

        # Ground + container body.
        d.rectangle([0, H - 60, W, H], fill=(180, 186, 192))
        cx0, cy0, cx1, cy1 = 60, 110, 580, 320
        d.rectangle([cx0, cy0, cx1, cy1], fill=body, outline=(30, 30, 30), width=4)
        # Corrugation lines.
        for x in range(cx0 + 20, cx1, 26):
            d.line([(x, cy0 + 6), (x, cy1 - 6)], fill=(0, 0, 0, 40), width=2)
        # Doors on the right.
        d.line([(cx1 - 120, cy0), (cx1 - 120, cy1)], fill=(20, 20, 20), width=3)
        d.rectangle([cx1 - 30, cy0 + 80, cx1 - 20, cy1 - 80], fill=(40, 40, 40))

        # ISO number.
        iso = f"PILU{rng.randint(100000, 999999)}{rng.randint(0, 9)}"
        d.text((cx0 + 20, cy0 + 20), iso, fill=(245, 245, 245), font=font)
        d.text((cx0 + 20, cy1 - 34), rng.choice(["40HC", "40GP", "20GP", "20RF"]),
                fill=(245, 245, 245), font=small)

        damage_type = "none"
        if damage in ("minor", "major"):
            damage_type = rng.choice(["dent", "rust", "door_misalignment"])
            _draw_damage(d, damage, damage_type, cx0, cy0, cx1, cy1, rng)

        fname = f"container_{i:04d}.png"
        img.save(os.path.join(out_dir, fname))
        gt.append({
            "file_name": fname,
            "container_no": iso,
            "gt_damage": damage,
            "gt_damage_type": damage_type,
        })
    return gt


def copy_real_container_images(samples_dir: str, out_dir: str) -> list[dict[str, Any]]:
    """Copy the bundled REAL container photos into ``out_dir`` and return their
    ground-truth label records (same schema as :func:`generate_container_images`).

    ``samples_dir`` is the repo's ``assets/container_samples`` directory, which
    holds the photos plus a ``labels.json`` mapping each file to its ground truth.
    These real photos AUGMENT the synthetic set — they land in the same
    container_images Volume and their labels are unioned into
    ``silver.container_image_labels`` so the vision agent classifies and scores
    them too. Best-effort: if the samples dir or labels file is missing, returns
    an empty list (the synthetic set still stands), so setup never halts.

    Only files listed in ``labels.json`` are copied (so an un-attributed image
    dropped into the folder is ignored until it's labelled).
    """
    import json
    import shutil

    labels_path = os.path.join(samples_dir, "labels.json")
    if not os.path.isfile(labels_path):
        return []
    try:
        with open(labels_path) as fh:
            spec = json.load(fh)
        records = spec.get("labels", []) if isinstance(spec, dict) else list(spec)
    except (ValueError, OSError):
        return []

    os.makedirs(out_dir, exist_ok=True)
    out: list[dict[str, Any]] = []
    for rec in records:
        fname = rec.get("file_name")
        if not fname:
            continue
        src = os.path.join(samples_dir, fname)
        if not os.path.isfile(src):
            continue  # labelled but the image isn't present — skip quietly
        shutil.copyfile(src, os.path.join(out_dir, fname))
        out.append({
            "file_name": fname,
            "container_no": rec.get("container_no"),
            "gt_damage": rec.get("gt_damage"),
            "gt_damage_type": rec.get("gt_damage_type") or "none",
        })
    return out


def _draw_damage(d, severity, dtype, cx0, cy0, cx1, cy1, rng) -> None:
    """Overlay a physically-plausible damage effect onto the container body.

    Minor vs major is distinguished by clearly-visible extent — count/size of
    dents, density of corrosion, size of the door gap — not by a few-pixel
    geometric offset, so both a human and a vision model can tell them apart.
    Dents are shaded (dark crease + light highlight rim) so they read as metal
    deformation rather than a flat painted circle; rust is an irregular
    corroded patch with streaks; door misalignment visibly displaces the door
    panel leaving a gap.
    """
    is_major = severity == "major"
    if dtype == "dent":
        # Wide, non-overlapping separation so severity is unambiguous:
        # minor = 1 small dent; major = 7-9 large dents covering the face.
        count = rng.randint(7, 9) if is_major else 1
        rmin, rmax = (44, 66) if is_major else (16, 24)
        for _ in range(count):
            r = rng.randint(rmin, rmax)
            x = rng.randint(cx0 + 40, cx1 - 150)
            y = rng.randint(cy0 + 24, cy1 - 24 - r // 2)
            h = max(10, r // 2)
            # Shadowed crease (deep gray) with a soft lighter rim + specular
            # highlight so the blob reads as a 3D inward dent, not a flat disc.
            d.ellipse([x - 2, y - 2, x + r + 2, y + h + 2], outline=(210, 210, 210), width=2)
            d.ellipse([x, y, x + r, y + h], fill=(58, 58, 62))
            d.ellipse([x + r // 5, y + h // 5, x + r // 2, y + h // 2], fill=(120, 120, 126))
    elif dtype == "rust":
        # minor = one small patch; major = a large, dense corroded region
        # (many overlapping patches) — clearly different coverage.
        patches = rng.randint(7, 10) if is_major else 1
        for _ in range(patches):
            base = rng.randint(30, 52) if is_major else rng.randint(9, 15)
            x = rng.randint(cx0 + 24, cx1 - 40 - base)
            y = rng.randint(cy0 + 20, cy1 - 24 - base)
            # Layered corrosion: dark core, mid-brown body, orange edge bleed +
            # a few drip streaks (rust runs downward on real containers).
            for rr, col in ((base, (150, 92, 44)), (int(base * 0.7), (110, 58, 24)),
                            (int(base * 0.4), (74, 38, 16))):
                d.ellipse([x, y, x + rr, y + rr], fill=col)
            for _ in range(3 if is_major else 1):
                sx = x + rng.randint(0, base)
                d.line([(sx, y + base // 2), (sx, y + base + rng.randint(6, 22))],
                       fill=(120, 62, 28), width=2)
    elif dtype == "door_misalignment":
        # The door panel occupies the right ~120px (see generate_container_images).
        # minor = slight drop, door still within the frame; major = large drop +
        # skew with the panel clearly jutting past the container's bottom edge.
        dx0 = cx1 - 120
        drop = 60 if is_major else 12
        skew = 34 if is_major else 6
        # Dark gap where the door has pulled away from the frame.
        d.rectangle([dx0 - 6, cy0, dx0 + 4, cy1], fill=(18, 18, 20))
        # The displaced door panel (skewed parallelogram, dropped by `drop`).
        panel = (30, 30, 34)
        d.polygon([
            (dx0 + 6, cy0 + drop), (cx1 + (16 if is_major else 0), cy0 + drop - skew // 2),
            (cx1 + (16 if is_major else 0), cy1 + drop - skew // 2), (dx0 + 6, cy1 + drop),
        ], fill=panel, outline=(200, 60, 40), width=4)
        # Red misalignment indicator along the hinge; for major it protrudes
        # far past the container edge to signal severity.
        over = 40 if is_major else 4
        d.line([(dx0, cy0 - over), (dx0, cy1 + over)], fill=(190, 40, 30), width=5)
