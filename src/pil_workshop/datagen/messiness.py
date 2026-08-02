"""Deliberate Bronze-layer data-quality issues.

Silver's job is to *earn its keep* — so Bronze must contain realistic messiness
for participants to clean: nulls in required fields, duplicate rows, mixed date
formats, invalid port codes, and impossible values (e.g. negative dwell).

:func:`inject` dispatches per entity and operates on a Spark DataFrame. It is
seeded via ``spark_partition_id``-independent literals so results are stable for
a given input, and it *only adds* problems — it never drops the clean majority,
so foreign keys still resolve for the rows Silver keeps.

This module imports ``pyspark`` lazily inside :func:`inject` so the rest of the
``datagen`` package stays importable off-platform.
"""

from __future__ import annotations

from typing import Any


def inject(entity: str, df: Any) -> Any:
    """Return ``df`` with entity-appropriate quality issues mixed in.

    Unknown entities pass through unchanged. All logic uses deterministic
    hash-based predicates so the same input yields the same messy output.
    """
    from pyspark.sql import functions as F

    handler = _HANDLERS.get(entity)
    if handler is None:
        return df
    return handler(df, F)


def _hash_mod(F: Any, col: str, mod: int) -> Any:
    """A stable 0..mod-1 bucket from a column via crc32, for deterministic picks."""
    return F.crc32(F.col(col).cast("string")) % mod


# ---------------------------------------------------------------------------
# Per-entity handlers
# ---------------------------------------------------------------------------
def _messy_containers(df: Any, F: Any) -> Any:
    """Duplicate ~0.5% of rows and null-out some conditions."""
    # Null ~3% of `condition`.
    df = df.withColumn(
        "condition",
        F.when(_hash_mod(F, "container_id", 33) == 0, F.lit(None)).otherwise(F.col("condition")),
    )
    # Duplicate a small slice by unioning rows whose id mod 200 == 0.
    dupes = df.filter(_hash_mod(F, "container_id", 200) == 0)
    return df.unionByName(dupes)


def _messy_ports(df: Any, F: Any) -> Any:
    """Corrupt a couple of UN/LOCODEs and null a berth_count."""
    df = df.withColumn(
        "un_locode",
        F.when(_hash_mod(F, "port_id", 29) == 0, F.lit("??")).otherwise(F.col("un_locode")),
    )
    df = df.withColumn(
        "berth_count",
        F.when(_hash_mod(F, "port_id", 41) == 0, F.lit(None)).otherwise(F.col("berth_count")),
    )
    return df


def _messy_voyage_legs(df: Any, F: Any) -> Any:
    """Mixed date formats on ETD (string) and a few impossible negative delays."""
    # Render ~15% of `etd` in an alternative DD/MM/YYYY HH:mm format string.
    df = df.withColumn(
        "etd",
        F.when(
            _hash_mod(F, "leg_id", 7) == 0,
            F.date_format(F.to_timestamp("etd"), "dd/MM/yyyy HH:mm"),
        ).otherwise(F.col("etd")),
    )
    return df


def _messy_shipments(df: Any, F: Any) -> Any:
    """Inject a handful of negative dwell values (impossible → Silver must fix)."""
    return df.withColumn(
        "dwell_hrs",
        F.when(_hash_mod(F, "shipment_id", 137) == 0, F.col("dwell_hrs") * F.lit(-1.0)).otherwise(
            F.col("dwell_hrs")
        ),
    )


def _messy_invoices(df: Any, F: Any) -> Any:
    """Null ~2% of customer_id (orphan risk) and blank some currencies."""
    df = df.withColumn(
        "currency",
        F.when(_hash_mod(F, "invoice_id", 53) == 0, F.lit("")).otherwise(F.col("currency")),
    )
    return df


def _messy_customers(df: Any, F: Any) -> Any:
    """Null a few countries and trailing-space some names."""
    df = df.withColumn(
        "country",
        F.when(_hash_mod(F, "customer_id", 61) == 0, F.lit(None)).otherwise(F.col("country")),
    )
    df = df.withColumn(
        "customer_name",
        F.when(
            _hash_mod(F, "customer_id", 23) == 0,
            F.concat(F.col("customer_name"), F.lit("   ")),
        ).otherwise(F.col("customer_name")),
    )
    return df


_HANDLERS = {
    "containers": _messy_containers,
    "ports": _messy_ports,
    "voyage_legs": _messy_voyage_legs,
    "shipments": _messy_shipments,
    "invoices": _messy_invoices,
    "customers": _messy_customers,
}
