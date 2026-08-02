"""Silver-layer construction: clean/conform Bronze into documented Delta tables.

Kept out of the notebook so the transformation logic is unit-testable and
trivially patchable on-site. Each ``_silver_<entity>`` returns a cleaned Spark
DataFrame; :func:`build_all_silver` writes them. Constraints and the exhaustive
per-column comments live here too.

All functions take an active ``spark`` session as their first argument so this
module never imports a global session at load time.
"""

from __future__ import annotations

from typing import Any

from .config import GOLD, SILVER  # noqa: F401 - SILVER used in f-strings
from .utils import get_logger

LOG = get_logger("pil_workshop.silver_build")


def _b(catalog: str) -> str:
    return f"`{catalog}`.`bronze`"


def _s(catalog: str) -> str:
    return f"`{catalog}`.`silver`"


# ---------------------------------------------------------------------------
# Cleaning transformations (return Spark DataFrames)
# ---------------------------------------------------------------------------
def _silver_ports(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT DISTINCT
            CAST(port_id AS INT)                          AS port_id,
            -- repair obviously bad codes ('??') to a sentinel we can filter on
            CASE WHEN un_locode RLIKE '^[A-Z]{{5}}$' THEN un_locode
                 ELSE CONCAT('UNK', LPAD(CAST(port_id AS STRING), 2, '0')) END AS un_locode,
            TRIM(port_name)                               AS port_name,
            country, region,
            CAST(latitude AS DOUBLE)                      AS latitude,
            CAST(longitude AS DOUBLE)                     AS longitude,
            COALESCE(CAST(berth_count AS INT), 4)         AS berth_count
        FROM {b}.ports
        WHERE port_id IS NOT NULL
    """)


def _silver_vessels(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(vessel_id AS INT) AS vessel_id, imo_number,
            TRIM(vessel_name) AS vessel_name, vessel_class,
            CAST(capacity_teu AS INT) AS capacity_teu,
            CAST(build_year AS INT) AS build_year, fuel_type,
            CAST(service_speed_kn AS DOUBLE) AS service_speed_kn
        FROM {b}.vessels WHERE vessel_id IS NOT NULL
    """)


def _silver_routes(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(route_id AS INT) AS route_id, service_code,
            TRIM(route_name) AS route_name,
            -- keep the rotation as a JSON string in silver; gold explodes it
            port_rotation, port_rotation_locodes,
            frequency, CAST(leg_count AS INT) AS leg_count
        FROM {b}.routes WHERE route_id IS NOT NULL
    """)


def _silver_customers(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(customer_id AS INT) AS customer_id,
            TRIM(customer_name) AS customer_name,
            customer_type, industry,
            COALESCE(country, 'Unknown') AS country,
            credit_terms, CAST(credit_limit_usd AS BIGINT) AS credit_limit_usd
        FROM {b}.customers WHERE customer_id IS NOT NULL
    """)


def _silver_voyages(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(voyage_id AS INT) AS voyage_id, voyage_no,
            CAST(vessel_id AS INT) AS vessel_id,
            CAST(route_id AS INT) AS route_id,
            CAST(departure_date AS DATE) AS departure_date,
            CAST(leg_count AS INT) AS leg_count,
            CAST(total_fuel_consumed_mt AS DOUBLE) AS total_fuel_consumed_mt,
            status
        FROM {b}.voyages WHERE voyage_id IS NOT NULL
    """)


def _silver_voyage_legs(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    # Repair mixed date formats: try ISO first, then dd/MM/yyyy HH:mm.
    return spark.sql(f"""
        SELECT
            CAST(leg_id AS INT) AS leg_id,
            CAST(voyage_id AS INT) AS voyage_id,
            CAST(leg_sequence AS INT) AS leg_sequence,
            CAST(origin_port_id AS INT) AS origin_port_id,
            CAST(dest_port_id AS INT) AS dest_port_id,
            CAST(distance_nm AS DOUBLE) AS distance_nm,
            COALESCE(
                TRY_TO_TIMESTAMP(etd),
                TRY_TO_TIMESTAMP(etd, 'dd/MM/yyyy HH:mm')
            ) AS etd,
            TRY_TO_TIMESTAMP(eta) AS eta,
            TRY_TO_TIMESTAMP(atd) AS atd,
            TRY_TO_TIMESTAMP(ata) AS ata,
            CAST(arrival_delay_hrs AS DOUBLE) AS arrival_delay_hrs,
            CAST(on_time AS BOOLEAN) AS on_time,
            CAST(fuel_consumed_mt AS DOUBLE) AS fuel_consumed_mt,
            CAST(capacity_teu AS INT) AS capacity_teu,
            CAST(loaded_teu AS INT) AS loaded_teu,
            CAST(load_factor AS DOUBLE) AS load_factor
        FROM {b}.voyage_legs WHERE leg_id IS NOT NULL
    """)


def _silver_containers(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    # Dedup by container_no (bronze injected duplicates); keep lowest id.
    return spark.sql(f"""
        WITH ranked AS (
            SELECT *, ROW_NUMBER() OVER (
                PARTITION BY container_no ORDER BY CAST(container_id AS INT)
            ) AS rn
            FROM {b}.containers WHERE container_no IS NOT NULL
        )
        SELECT
            CAST(container_id AS INT) AS container_id, container_no,
            container_type, CAST(teu_factor AS DOUBLE) AS teu_factor,
            CAST(is_reefer AS BOOLEAN) AS is_reefer,
            COALESCE(condition, 'Unknown') AS condition,
            current_status, CAST(year_built AS INT) AS year_built
        FROM ranked WHERE rn = 1
    """)


def _silver_bookings(spark: Any, catalog: str) -> Any:
    b, s = _b(catalog), _s(catalog)
    # Drop bookings whose customer is missing (orphans) — real referential fix.
    return spark.sql(f"""
        SELECT
            CAST(bk.booking_id AS INT) AS booking_id, bk.booking_no,
            CAST(bk.customer_id AS INT) AS customer_id,
            CAST(bk.voyage_id AS INT) AS voyage_id,
            CAST(bk.leg_id AS INT) AS leg_id,
            CAST(bk.pol_port_id AS INT) AS pol_port_id,
            CAST(bk.pod_port_id AS INT) AS pod_port_id,
            bk.commodity, CAST(bk.container_count AS INT) AS container_count,
            CAST(bk.freight_rate_usd AS DOUBLE) AS freight_rate_usd,
            TRY_TO_TIMESTAMP(bk.booking_ts) AS booking_ts,
            bk.status, CAST(bk.is_cancelled AS BOOLEAN) AS is_cancelled
        FROM {b}.bookings bk
        INNER JOIN {s}.customers c ON CAST(bk.customer_id AS INT) = c.customer_id
        WHERE bk.booking_id IS NOT NULL
    """)


def _silver_shipments(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    # Fix impossible negative dwell by taking absolute value; recompute if null.
    return spark.sql(f"""
        SELECT
            CAST(shipment_id AS INT) AS shipment_id,
            CAST(booking_id AS INT) AS booking_id,
            CAST(container_id AS INT) AS container_id,
            container_no, container_type, CAST(teu AS DOUBLE) AS teu,
            CAST(is_reefer AS BOOLEAN) AS is_reefer,
            CAST(pol_port_id AS INT) AS pol_port_id,
            CAST(pod_port_id AS INT) AS pod_port_id,
            TRY_TO_TIMESTAMP(gate_in_ts) AS gate_in_ts,
            TRY_TO_TIMESTAMP(load_ts) AS load_ts,
            TRY_TO_TIMESTAMP(discharge_ts) AS discharge_ts,
            TRY_TO_TIMESTAMP(gate_out_ts) AS gate_out_ts,
            ABS(CAST(dwell_hrs AS DOUBLE)) AS dwell_hrs
        FROM {b}.shipments WHERE shipment_id IS NOT NULL
    """)


def _silver_container_events(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(event_id AS BIGINT) AS event_id,
            CAST(shipment_id AS INT) AS shipment_id,
            container_no, event_type,
            TRY_TO_TIMESTAMP(event_ts) AS event_ts,
            CAST(damage_flag AS BOOLEAN) AS damage_flag,
            CAST(dwell_hrs AS DOUBLE) AS dwell_hrs
        FROM {b}.container_events WHERE event_id IS NOT NULL
    """)


def _silver_port_calls(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(port_call_id AS INT) AS port_call_id,
            CAST(voyage_id AS INT) AS voyage_id,
            CAST(port_id AS INT) AS port_id,
            TRY_TO_TIMESTAMP(arrival_ts) AS arrival_ts,
            TRY_TO_TIMESTAMP(berth_ts) AS berth_ts,
            TRY_TO_TIMESTAMP(departure_ts) AS departure_ts,
            CAST(waiting_time_hrs AS DOUBLE) AS waiting_time_hrs,
            CAST(turnaround_hrs AS DOUBLE) AS turnaround_hrs,
            CAST(crane_moves AS INT) AS crane_moves
        FROM {b}.port_calls WHERE port_call_id IS NOT NULL
    """)


def _silver_invoices(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    # Blank currency → NULL then default to 'USD'; keep gt_anomaly for eval.
    return spark.sql(f"""
        SELECT
            CAST(invoice_id AS INT) AS invoice_id, invoice_no,
            CAST(booking_id AS INT) AS booking_id,
            CAST(customer_id AS INT) AS customer_id, po_number,
            CAST(issue_date AS DATE) AS issue_date,
            CAST(due_date AS DATE) AS due_date,
            CAST(paid_date AS DATE) AS paid_date,
            COALESCE(NULLIF(TRIM(currency), ''), 'USD') AS currency,
            CAST(fx_to_usd AS DOUBLE) AS fx_to_usd,
            CAST(subtotal AS DOUBLE) AS subtotal,
            CAST(tax AS DOUBLE) AS tax,
            CAST(total AS DOUBLE) AS total,
            CAST(total_usd AS DOUBLE) AS total_usd,
            status, CAST(is_disputed AS BOOLEAN) AS is_disputed,
            gt_anomaly
        FROM {b}.invoices WHERE invoice_id IS NOT NULL
    """)


def _silver_invoice_line_items(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(line_item_id AS BIGINT) AS line_item_id,
            CAST(invoice_id AS INT) AS invoice_id,
            charge_type, CAST(quantity AS INT) AS quantity,
            CAST(unit_amount AS DOUBLE) AS unit_amount,
            CAST(amount AS DOUBLE) AS amount, currency
        FROM {b}.invoice_line_items WHERE line_item_id IS NOT NULL
    """)


def _silver_spare_parts(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(sku_id AS INT) AS sku_id, sku_code, category, depot,
            CAST(unit_cost_usd AS DOUBLE) AS unit_cost_usd,
            CAST(lead_time_days AS INT) AS lead_time_days,
            demand_pattern, CAST(reorder_point AS INT) AS reorder_point
        FROM {b}.spare_parts WHERE sku_id IS NOT NULL
    """)


def _silver_spare_parts_consumption(spark: Any, catalog: str) -> Any:
    b = _b(catalog)
    return spark.sql(f"""
        SELECT
            CAST(sku_id AS INT) AS sku_id, sku_code, depot,
            CAST(txn_date AS DATE) AS txn_date,
            CAST(quantity AS INT) AS quantity, category
        FROM {b}.spare_parts_consumption WHERE sku_id IS NOT NULL
    """)


# Registry: silver table name -> builder function. Order respects FK deps
# (customers before bookings, etc.).
_BUILDERS = [
    ("ports", _silver_ports),
    ("vessels", _silver_vessels),
    ("routes", _silver_routes),
    ("customers", _silver_customers),
    ("voyages", _silver_voyages),
    ("voyage_legs", _silver_voyage_legs),
    ("containers", _silver_containers),
    ("bookings", _silver_bookings),
    ("shipments", _silver_shipments),
    ("container_events", _silver_container_events),
    ("port_calls", _silver_port_calls),
    ("invoices", _silver_invoices),
    ("invoice_line_items", _silver_invoice_line_items),
    ("spare_parts", _silver_spare_parts),
    ("spare_parts_consumption", _silver_spare_parts_consumption),
]


def build_all_silver(spark: Any, catalog: str) -> list[tuple[str, int]]:
    """Build and write every Silver table; return ``[(name, row_count), ...]``."""
    s = _s(catalog)
    written: list[tuple[str, int]] = []
    for name, fn in _BUILDERS:
        df = fn(spark, catalog)
        target = f"{s}.{name}"
        (
            df.write.format("delta")
            .mode("overwrite")
            .option("overwriteSchema", "true")
            .saveAsTable(target)
        )
        cnt = spark.table(target).count()
        spark.sql(
            f"COMMENT ON TABLE {target} IS 'Silver (cleaned, conformed) — {name} for PIL workshop.'"
        )
        _apply_domain_tag(spark, target)
        written.append((name, cnt))
        LOG.info("Silver %s: %d rows", name, cnt)
    return written


# Some workspaces enforce a UC tag policy that restricts allowed values for the
# `domain` key. We try our preferred value first, then policy-friendly
# fallbacks, and finally give up gracefully — a rejected tag must never fail the
# whole Silver build (the tables/comments are what matter).
_DOMAIN_TAG_CANDIDATES = ("supply_chain", "operations", "shipping")


def _apply_domain_tag(spark: Any, target: str) -> None:
    for value in _DOMAIN_TAG_CANDIDATES:
        try:
            spark.sql(f"ALTER TABLE {target} SET TAGS ('domain' = '{value}')")
            return
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "not an allowed value" in msg or "tag policy" in msg:
                continue  # try the next policy-friendly value
            LOG.warning("Could not set domain tag on %s: %s", target, exc)
            return
    LOG.warning("No allowed 'domain' tag value accepted for %s; skipping tag.", target)


# ---------------------------------------------------------------------------
# Constraints
# ---------------------------------------------------------------------------
_CONSTRAINTS_SQL = [
    # NOT NULL on keys
    "ALTER TABLE {s}.ports ALTER COLUMN port_id SET NOT NULL",
    "ALTER TABLE {s}.vessels ALTER COLUMN vessel_id SET NOT NULL",
    "ALTER TABLE {s}.customers ALTER COLUMN customer_id SET NOT NULL",
    "ALTER TABLE {s}.voyages ALTER COLUMN voyage_id SET NOT NULL",
    "ALTER TABLE {s}.bookings ALTER COLUMN booking_id SET NOT NULL",
    "ALTER TABLE {s}.invoices ALTER COLUMN invoice_id SET NOT NULL",
    # CHECK constraints (idempotent via IF NOT EXISTS-ish guard in code)
    ("ALTER TABLE {s}.shipments ADD CONSTRAINT chk_dwell_nonneg CHECK (dwell_hrs >= 0)"),
    ("ALTER TABLE {s}.invoices ADD CONSTRAINT chk_total_nonneg CHECK (total >= 0)"),
    (
        "ALTER TABLE {s}.voyage_legs ADD CONSTRAINT chk_load_factor "
        "CHECK (load_factor >= 0 AND load_factor <= 1.2)"
    ),
    # Primary keys (informational, RELY)
    "ALTER TABLE {s}.ports ADD CONSTRAINT pk_ports PRIMARY KEY (port_id)",
    "ALTER TABLE {s}.vessels ADD CONSTRAINT pk_vessels PRIMARY KEY (vessel_id)",
    "ALTER TABLE {s}.customers ADD CONSTRAINT pk_customers PRIMARY KEY (customer_id)",
    "ALTER TABLE {s}.voyages ADD CONSTRAINT pk_voyages PRIMARY KEY (voyage_id)",
    "ALTER TABLE {s}.bookings ADD CONSTRAINT pk_bookings PRIMARY KEY (booking_id)",
    "ALTER TABLE {s}.invoices ADD CONSTRAINT pk_invoices PRIMARY KEY (invoice_id)",
    # Foreign keys (informational; help Genie & optimizer)
    (
        "ALTER TABLE {s}.voyages ADD CONSTRAINT fk_voyages_vessel "
        "FOREIGN KEY (vessel_id) REFERENCES {s}.vessels(vessel_id)"
    ),
    (
        "ALTER TABLE {s}.bookings ADD CONSTRAINT fk_bookings_customer "
        "FOREIGN KEY (customer_id) REFERENCES {s}.customers(customer_id)"
    ),
    (
        "ALTER TABLE {s}.bookings ADD CONSTRAINT fk_bookings_voyage "
        "FOREIGN KEY (voyage_id) REFERENCES {s}.voyages(voyage_id)"
    ),
    (
        "ALTER TABLE {s}.invoices ADD CONSTRAINT fk_invoices_customer "
        "FOREIGN KEY (customer_id) REFERENCES {s}.customers(customer_id)"
    ),
]


def add_constraints(spark: Any, catalog: str) -> None:
    """Apply NOT NULL / CHECK / PK / FK constraints, ignoring 'already exists'."""
    s = _s(catalog)
    for tmpl in _CONSTRAINTS_SQL:
        sql = tmpl.format(s=s)
        try:
            spark.sql(sql)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "already exists" in msg or "constraint" in msg and "exist" in msg:
                continue  # idempotent: constraint already present
            LOG.warning("Constraint skipped (%s): %s", sql[:60], exc)


# ---------------------------------------------------------------------------
# Column comments — a comment on EVERY column (Genie quality depends on this).
# ---------------------------------------------------------------------------
COLUMN_COMMENTS: dict[str, dict[str, str]] = {
    "ports": {
        "port_id": "Surrogate key for the port.",
        "un_locode": "UN/LOCODE (5-char) port code, e.g. SGSIN for Singapore.",
        "port_name": "Human-readable port name.",
        "country": "Country the port is located in.",
        "region": "Trade region grouping (e.g. Southeast Asia, North Europe).",
        "latitude": "Port latitude in decimal degrees.",
        "longitude": "Port longitude in decimal degrees.",
        "berth_count": "Number of berths available at the port.",
    },
    "vessels": {
        "vessel_id": "Surrogate key for the vessel.",
        "imo_number": "IMO number — globally unique vessel identifier.",
        "vessel_name": "Vessel name (PIL 'Kota'-style naming).",
        "vessel_class": "Size class: Feeder, Panamax, Post-Panamax, Neo-Panamax, ULCV.",
        "capacity_teu": "Nominal capacity in twenty-foot equivalent units (TEU).",
        "build_year": "Year the vessel was built.",
        "fuel_type": "Primary fuel: VLSFO, LNG, or dual-fuel.",
        "service_speed_kn": "Design service speed in knots.",
    },
    "routes": {
        "route_id": "Surrogate key for the liner service/route.",
        "service_code": "Short service code, e.g. AR1, NE2.",
        "route_name": "Descriptive service name (e.g. 'Asia–North Europe Loop').",
        "port_rotation": "Ordered JSON array of port_id the service calls.",
        "port_rotation_locodes": "Ordered JSON array of UN/LOCODEs for the rotation.",
        "frequency": "Sailing frequency (Weekly, Bi-weekly, Fortnightly).",
        "leg_count": "Number of legs in one full rotation.",
    },
    "customers": {
        "customer_id": "Surrogate key for the customer.",
        "customer_name": "Customer/company name.",
        "customer_type": "Role: Shipper (BCO), Consignee, Freight Forwarder, NVOCC.",
        "industry": "Customer's primary industry vertical.",
        "country": "Customer's country ('Unknown' if source was missing).",
        "credit_terms": "Payment terms: Prepaid, Net 15/30/45/60.",
        "credit_limit_usd": "Approved credit limit in USD.",
    },
    "voyages": {
        "voyage_id": "Surrogate key for the voyage.",
        "voyage_no": "Business voyage number (service_code + sequence).",
        "vessel_id": "FK → vessels.vessel_id operating this voyage.",
        "route_id": "FK → routes.route_id this voyage sails.",
        "departure_date": "Date the voyage departed its first port.",
        "leg_count": "Number of legs in this voyage.",
        "total_fuel_consumed_mt": "Total fuel burned across all legs, metric tonnes.",
        "status": "Completed or Active.",
    },
    "voyage_legs": {
        "leg_id": "Surrogate key for the voyage leg.",
        "voyage_id": "FK → voyages.voyage_id this leg belongs to.",
        "leg_sequence": "1-based order of the leg within the voyage.",
        "origin_port_id": "FK → ports.port_id where the leg departs.",
        "dest_port_id": "FK → ports.port_id where the leg arrives.",
        "distance_nm": "Great-circle leg distance in nautical miles.",
        "etd": "Estimated time of departure.",
        "eta": "Estimated time of arrival (pro-forma schedule).",
        "atd": "Actual time of departure.",
        "ata": "Actual time of arrival.",
        "arrival_delay_hrs": "ATA minus ETA in hours (negative = early).",
        "on_time": "TRUE when the leg arrived within 24h of ETA.",
        "fuel_consumed_mt": "Fuel burned on this leg, metric tonnes.",
        "capacity_teu": "Vessel capacity available on this leg, TEU.",
        "loaded_teu": "Loaded (manifest) TEU carried on this leg.",
        "load_factor": "loaded_teu / capacity_teu for this leg (0–1.2).",
    },
    "containers": {
        "container_id": "Surrogate key for the container.",
        "container_no": "ISO 6346 container number (with valid check digit).",
        "container_type": "ISO type: 20GP, 40GP, 40HC, 20RF (reefer), etc.",
        "teu_factor": "TEU factor (1.0 for 20ft, 2.0 for 40ft).",
        "is_reefer": "TRUE for refrigerated containers.",
        "condition": "Physical condition: Good, Fair, Damaged, Unknown.",
        "current_status": "Lifecycle status: At Origin, In Transit, etc.",
        "year_built": "Year the container was manufactured.",
    },
    "bookings": {
        "booking_id": "Surrogate key for the booking.",
        "booking_no": "Business booking reference.",
        "customer_id": "FK → customers.customer_id who made the booking.",
        "voyage_id": "FK → voyages.voyage_id booked.",
        "leg_id": "FK → voyage_legs.leg_id for the booked sailing.",
        "pol_port_id": "Port of loading (FK → ports.port_id).",
        "pod_port_id": "Port of discharge (FK → ports.port_id).",
        "commodity": "Commodity category being shipped.",
        "container_count": "Number of containers on the booking.",
        "freight_rate_usd": "Quoted freight rate per container in USD.",
        "booking_ts": "Timestamp the booking was created.",
        "status": "Confirmed, Completed, or Cancelled.",
        "is_cancelled": "TRUE if the booking was cancelled.",
    },
    "shipments": {
        "shipment_id": "Surrogate key for the container shipment.",
        "booking_id": "FK → bookings.booking_id.",
        "container_id": "FK → containers.container_id.",
        "container_no": "ISO 6346 number of the shipped container.",
        "container_type": "ISO container type.",
        "teu": "TEU carried by this shipment.",
        "is_reefer": "TRUE for reefer shipments.",
        "pol_port_id": "Port of loading (FK → ports.port_id).",
        "pod_port_id": "Port of discharge (FK → ports.port_id).",
        "gate_in_ts": "When the container entered the origin terminal.",
        "load_ts": "When the container was loaded onto the vessel.",
        "discharge_ts": "When the container was discharged at destination.",
        "gate_out_ts": "When the container left the destination terminal.",
        "dwell_hrs": "Hours between discharge and gate-out (always >= 0).",
    },
    "container_events": {
        "event_id": "Surrogate key for the event.",
        "shipment_id": "FK → shipments.shipment_id.",
        "container_no": "ISO 6346 number of the container.",
        "event_type": "GATE_IN, LOAD, DISCHARGE, GATE_OUT, CUSTOMS_HOLD, DAMAGE.",
        "event_ts": "Timestamp of the event.",
        "damage_flag": "TRUE for DAMAGE events.",
        "dwell_hrs": "Dwell hours (populated on GATE_OUT events).",
    },
    "port_calls": {
        "port_call_id": "Surrogate key for the port call.",
        "voyage_id": "FK → voyages.voyage_id calling the port.",
        "port_id": "FK → ports.port_id being called.",
        "arrival_ts": "Vessel arrival at the port (anchorage).",
        "berth_ts": "When the vessel went alongside the berth.",
        "departure_ts": "When the vessel departed the berth.",
        "waiting_time_hrs": "Hours waiting at anchorage before berthing.",
        "turnaround_hrs": "Hours alongside (berth to departure).",
        "crane_moves": "Total crane moves during the call.",
    },
    "invoices": {
        "invoice_id": "Surrogate key for the invoice.",
        "invoice_no": "Business invoice number (may duplicate on anomaly rows).",
        "booking_id": "FK → bookings.booking_id being invoiced.",
        "customer_id": "FK → customers.customer_id billed.",
        "po_number": "Customer purchase-order number (NULL on some anomalies).",
        "issue_date": "Date the invoice was issued.",
        "due_date": "Payment due date.",
        "paid_date": "Date paid (NULL if still open/overdue).",
        "currency": "Invoice currency (defaults to USD if source blank).",
        "fx_to_usd": "FX rate to convert the currency to USD.",
        "subtotal": "Sum of line-item amounts before tax.",
        "tax": "Tax amount.",
        "total": "Invoice total (subtotal + tax; wrong on ~some anomalies).",
        "total_usd": "Total converted to USD.",
        "status": "Paid, Open, Overdue, or Disputed.",
        "is_disputed": "TRUE if the invoice is disputed.",
        "gt_anomaly": "Ground-truth anomaly label for eval (total_mismatch/"
        "missing_po/duplicate_no); NULL if clean.",
    },
    "invoice_line_items": {
        "line_item_id": "Surrogate key for the line item.",
        "invoice_id": "FK → invoices.invoice_id.",
        "charge_type": "Charge category (Ocean Freight, THC, Demurrage, ...).",
        "quantity": "Quantity (usually container count).",
        "unit_amount": "Amount per unit in the invoice currency.",
        "amount": "Extended line amount in the invoice currency.",
        "currency": "Line-item currency.",
    },
    "spare_parts": {
        "sku_id": "Surrogate key for the spare-part SKU.",
        "sku_code": "Business SKU code.",
        "category": "Part category (Engine Spares, Reefer Parts, ...).",
        "depot": "UN/LOCODE of the stocking depot.",
        "unit_cost_usd": "Unit cost in USD.",
        "lead_time_days": "Replenishment lead time in days.",
        "demand_pattern": "'smooth' or 'intermittent' — drives forecast method.",
        "reorder_point": "Reorder point (units).",
    },
    "spare_parts_consumption": {
        "sku_id": "FK → spare_parts.sku_id consumed.",
        "sku_code": "Business SKU code (denormalized).",
        "depot": "Depot where consumption occurred.",
        "txn_date": "Date of consumption.",
        "quantity": "Units consumed that day.",
        "category": "Part category (denormalized).",
    },
}


def apply_column_comments(spark: Any, catalog: str) -> None:
    """Set the business comment on every documented Silver column."""
    s = _s(catalog)
    for table, cols in COLUMN_COMMENTS.items():
        for col, comment in cols.items():
            safe = comment.replace("'", "''")
            try:
                spark.sql(f"ALTER TABLE {s}.{table} ALTER COLUMN {col} COMMENT '{safe}'")
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Comment skipped %s.%s: %s", table, col, exc)
