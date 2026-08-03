"""FastAPI dependency providers — wire services to their external clients.

Kept tiny and import-light so tests can override with ``app.dependency_overrides``.
"""

from __future__ import annotations

from backend.services.analytics_service import AnalyticsService
from backend.services.clients import (
    lakebase_connection,
    sql_execute,
    sql_query,
    sql_query_strict,
    workspace_client,
)
from backend.services.extraction_service import ExtractionService
from backend.services.inspection_service import InspectionService
from backend.services.invoice_service import InvoiceService


def get_invoice_service() -> InvoiceService:
    return InvoiceService(conn_factory=lakebase_connection, sql_fn=sql_query)


def get_extraction_service() -> ExtractionService:
    # Strict SQL: extraction must surface real errors, not silently return [].
    # The FMAPI endpoint lives in the governed UC function, not here.
    # write_fn persists each extraction to the Delta sink (best-effort).
    return ExtractionService(
        workspace_client=workspace_client(),
        sql_fn=sql_query_strict,
        write_fn=sql_execute,
    )


def get_inspection_service() -> InspectionService:
    return InspectionService(conn_factory=lakebase_connection, sql_fn=sql_query)


def get_analytics_service() -> AnalyticsService:
    return AnalyticsService(
        sql_fn=sql_query,
        invoice_service=get_invoice_service(),
        inspection_service=get_inspection_service(),
    )
