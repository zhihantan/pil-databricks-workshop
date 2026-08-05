"""Central configuration for the PIL Databricks Workshop.

This module is intentionally free of any Databricks runtime imports so it can
be imported anywhere (notebooks, the FastAPI app, and unit tests running off
platform). Names defined here are the single source of truth for catalog,
schema, and volume identifiers, the workshop color palette, data-generation
scale presets, and the KPI target ranges used by the smoke tests.

Override any of the string defaults via notebook widgets / environment
variables using :func:`resolve` in ``pil_workshop.utils``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# Deployment target (see MASTER_PROMPT: Azure Databricks, southeastasia)
# ---------------------------------------------------------------------------
CLOUD = "azure"
REGION = "southeastasia"

# ---------------------------------------------------------------------------
# Unity Catalog namespace
# ---------------------------------------------------------------------------
DEFAULT_CATALOG = "pil_workshop"

# Schema names. `apps` and `ml` join the classic medallion trio.
SCHEMAS: tuple[str, ...] = ("bronze", "silver", "gold", "apps", "ml")
BRONZE, SILVER, GOLD, APPS, ML = SCHEMAS

# Volumes (created inside the bronze schema). Values are the volume *names*;
# full paths are built with `volume_path()`.
VOLUME_RAW = "raw_files"  # landed JSON/CSV for structured entities
VOLUME_INVOICES = "raw_invoices"  # synthetic freight-invoice PDFs
VOLUME_IMAGES = "container_images"  # synthetic labeled container images
VOLUMES: tuple[str, ...] = (VOLUME_RAW, VOLUME_INVOICES, VOLUME_IMAGES)

# Databricks App name (used by 10_deploy_app and to resolve the app's service
# principal when scoping AI-usage views to this project's agents).
APP_NAME = "pil-invoice-vision"
# Lakebase instance name (app mints its own credential via the SDK).
LAKEBASE_INSTANCE = "pil-workshop-db"

# Deterministic seed for every random generator in the repo.
SEED = 42

# ---------------------------------------------------------------------------
# Workshop color palette — defined ONCE, reused by the dashboard JSON, the
# frontend design system, and any notebook chart.
# ---------------------------------------------------------------------------
PALETTE = {
    "navy": "#0B1F3A",  # primary / headers
    "teal": "#0E7C86",  # accent / positive series
    "amber": "#F5A623",  # signal / highlight
    "offwhite": "#F7F9FB",  # background
    "ink": "#122031",  # body text
    "slate": "#5B6B7B",  # muted text / gridlines
    "seafoam": "#5BB8B0",  # secondary series
    "coral": "#E4572E",  # negative / alert series
    "sand": "#E9DCC3",  # neutral fill
}
# Ordered categorical sequence for charts (accessible, distinct in light/dark).
PALETTE_SEQUENCE = [
    PALETTE["teal"],
    PALETTE["amber"],
    PALETTE["navy"],
    PALETTE["seafoam"],
    PALETTE["coral"],
    PALETTE["slate"],
]

# ---------------------------------------------------------------------------
# Relative-date anchoring — history always ends "yesterday" so the workshop
# looks fresh no matter when it is run.
# ---------------------------------------------------------------------------
HISTORY_MONTHS = 24


def history_window(today: date | None = None) -> tuple[date, date]:
    """Return ``(start_date, end_date)`` for the synthetic history window.

    ``end_date`` is yesterday relative to ``today`` (defaults to the real
    current date); ``start_date`` is ``HISTORY_MONTHS`` earlier.
    """
    anchor = today or date.today()
    end = anchor - timedelta(days=1)
    start = end - timedelta(days=int(HISTORY_MONTHS * 30.4375))
    return start, end


# ---------------------------------------------------------------------------
# Data-generation scale presets. `demo` keeps a full setup under a few minutes
# on serverless; `full` matches the master-prompt volumes for a realistic feel.
# The setup notebooks expose a `scale` widget selecting one of these.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DataScale:
    """Row-count targets for each synthetic entity at a given scale."""

    name: str
    vessels: int
    ports: int
    routes: int
    customers: int
    voyages: int
    containers: int
    bookings: int
    container_events: int
    port_calls: int
    invoices: int
    spare_parts_skus: int
    inventory_days: int
    # unstructured
    invoice_pdfs: int
    container_images: int


SCALES: dict[str, DataScale] = {
    "demo": DataScale(
        name="demo",
        vessels=100,
        ports=60,
        routes=25,
        customers=800,
        voyages=1500,
        containers=12000,
        bookings=40000,
        container_events=300000,
        port_calls=8000,
        invoices=15000,
        spare_parts_skus=1200,
        inventory_days=540,
        invoice_pdfs=60,
        container_images=48,
    ),
    "full": DataScale(
        name="full",
        vessels=100,
        ports=60,
        routes=25,
        customers=800,
        voyages=6000,
        containers=50000,
        bookings=400000,
        container_events=3000000,
        port_calls=30000,
        invoices=60000,
        spare_parts_skus=5000,
        inventory_days=730,
        invoice_pdfs=200,
        container_images=160,
    ),
}
DEFAULT_SCALE = "demo"


def get_scale(name: str | None = None) -> DataScale:
    """Return the :class:`DataScale` for ``name`` (falls back to the default)."""
    return SCALES.get((name or DEFAULT_SCALE).strip().lower(), SCALES[DEFAULT_SCALE])


# ---------------------------------------------------------------------------
# KPI plausibility ranges — asserted by the smoke tests in 04_build_gold so a
# facilitator can trust the numbers on screen. (inclusive lo, hi)
# ---------------------------------------------------------------------------
KPI_RANGES: dict[str, tuple[float, float]] = {
    "schedule_reliability_pct": (60.0, 85.0),
    "on_time_delivery_pct": (65.0, 90.0),
    "vessel_utilization_pct": (70.0, 95.0),
    "avg_port_dwell_hrs": (18.0, 96.0),
    "avg_turnaround_hrs": (12.0, 60.0),
    "revenue_per_teu_usd": (600.0, 2600.0),
    "booking_cancellation_rate_pct": (2.0, 12.0),
    "dso_days": (30.0, 75.0),
    "fuel_efficiency_mt_per_1k_teu_nm": (0.02, 0.25),
}

# ---------------------------------------------------------------------------
# Naming helpers
# ---------------------------------------------------------------------------


def three_level(catalog: str, schema: str, obj: str) -> str:
    """Backtick-quote a three-level Unity Catalog name."""
    return f"`{catalog}`.`{schema}`.`{obj}`"


def volume_path(catalog: str, volume: str, *parts: str, schema: str = BRONZE) -> str:
    """Build a ``/Volumes/...`` path, optionally with sub-path ``parts``."""
    base = f"/Volumes/{catalog}/{schema}/{volume}"
    return "/".join([base, *[p.strip("/") for p in parts]]) if parts else base


# Canonical table inventories per layer (used by teardown, docs, and tests to
# stay in lockstep with what the build notebooks create).
SILVER_TABLES: tuple[str, ...] = (
    "vessels",
    "ports",
    "routes",
    "customers",
    "voyages",
    "voyage_legs",
    "containers",
    "bookings",
    "shipments",
    "container_events",
    "port_calls",
    "invoices",
    "invoice_line_items",
    "spare_parts",
    "spare_parts_consumption",
    "invoice_extractions",
    "container_inspections",
)
GOLD_TABLES: tuple[str, ...] = (
    "invoice_exceptions",
    "demand_forecasts",
    "repositioning_plan",
    "invoice_decisions_synced",
)
GOLD_MATERIALIZED_VIEWS: tuple[str, ...] = (
    "mv_daily_operations_kpis",
    "mv_port_performance",
    "mv_customer_revenue",
    "mv_container_utilization",
)
GOLD_METRIC_VIEWS: tuple[str, ...] = (
    "metric_schedule_reliability",
    "metric_delivery_transit",
    "metric_vessel_utilization",
    "metric_port_dwell_turnaround",
    "metric_revenue",
    "metric_sustainability",
    "metric_working_capital",
    "metric_booking_cancellation",
    "metric_container_dwell",
)
GOLD_USAGE_VIEWS: tuple[str, ...] = (
    "v_ai_usage_daily",
    "v_ai_usage_by_endpoint",
    "v_ai_usage_by_user",
)
# Analytics views backing the Genie Code build-your-own-dashboard pages (L2-L4).
GOLD_ANALYTICS_VIEWS: tuple[str, ...] = (
    "v_financial_health",
    "v_inventory_planning",
    "v_repositioning_summary",
)


@dataclass(frozen=True)
class WorkshopConfig:
    """Resolved configuration passed around the setup notebooks."""

    catalog: str = DEFAULT_CATALOG
    scale: str = DEFAULT_SCALE
    schemas: tuple[str, ...] = field(default_factory=lambda: SCHEMAS)

    def scale_spec(self) -> DataScale:
        return get_scale(self.scale)
