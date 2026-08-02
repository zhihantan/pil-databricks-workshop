"""Invoice review service: queue listing, decisions, PDF preview URLs.

Backed by Lakebase when available; otherwise an in-memory demo queue seeded from
UC (or a small static sample) so the app is fully clickable in a workshop even
before Lakebase is provisioned.
"""

from __future__ import annotations

from typing import Any

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.models.schemas import InvoiceDecisionRequest, InvoiceQueueItem
from backend.services import demo_store

LOG = get_logger("backend.invoice_service")


class InvoiceService:
    """Data access for the invoice review queue and decisions.

    ``conn_factory`` returns a live Lakebase connection or None; ``sql_fn`` runs
    a UC read. Both are injected so tests can pass fakes.
    """

    def __init__(self, conn_factory: Any = None, sql_fn: Any = None) -> None:
        self._conn_factory = conn_factory
        self._sql_fn = sql_fn

    # ---- queue -----------------------------------------------------------
    def list_queue(self, status: str | None = None) -> list[InvoiceQueueItem]:
        conn = self._conn_factory() if self._conn_factory else None
        if conn is not None:
            rows = self._list_from_lakebase(conn, status)
        else:
            rows = self._list_from_memory(status)
        items = []
        for r in rows:
            item = InvoiceQueueItem(**r)
            item.pdf_preview_url = f"/api/invoices/{item.file_name}/pdf"
            items.append(item)
        return items

    def _list_from_lakebase(self, conn: Any, status: str | None) -> list[dict[str, Any]]:
        try:
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        "SELECT id, file_name, invoice_no, customer, extracted_total, "
                        "ground_truth_total, exception_type, status "
                        "FROM pil_app.invoice_review_queue WHERE status=%s "
                        "ORDER BY id", (status,))
                else:
                    cur.execute(
                        "SELECT id, file_name, invoice_no, customer, extracted_total, "
                        "ground_truth_total, exception_type, status "
                        "FROM pil_app.invoice_review_queue ORDER BY id")
                cols = [c[0] for c in cur.description]
                return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Lakebase queue read failed, using memory: %s", exc)
            return self._list_from_memory(status)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _ensure_demo_seeded(self) -> None:
        """Seed the process-level demo store once (from UC, else static sample).

        Using the shared demo_store (not per-instance state) means a decision
        made in one request is visible in the next — services are constructed
        fresh per request, so instance state would otherwise be lost.
        """
        if demo_store.is_seeded():
            return
        settings = get_settings()
        rows: list[dict[str, Any]] = []
        if self._sql_fn:
            try:
                uc = self._sql_fn(
                    f"SELECT file_name, invoice_no, customer, total AS extracted_total, "
                    f"gt_total AS ground_truth_total, exception_type "
                    f"FROM {settings.gold}.invoice_exceptions LIMIT 200")
                rows = list(uc)
            except Exception as exc:  # noqa: BLE001
                LOG.warning("UC seed failed: %s", exc)
        demo_store.seed_queue(rows or _SAMPLE_QUEUE)

    def _list_from_memory(self, status: str | None) -> list[dict[str, Any]]:
        self._ensure_demo_seeded()
        return demo_store.list_queue(status)

    # ---- decisions -------------------------------------------------------
    def decide(self, file_name: str, req: InvoiceDecisionRequest, actor: str) -> dict[str, Any]:
        conn = self._conn_factory() if self._conn_factory else None
        if conn is not None:
            return self._decide_lakebase(conn, file_name, req, actor)
        return self._decide_memory(file_name, req)

    def _decide_lakebase(self, conn: Any, file_name: str,
                        req: InvoiceDecisionRequest, actor: str) -> dict[str, Any]:
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pil_app.invoice_decisions "
                    "(file_name, decision, reason, adjusted_total, decided_by) "
                    "VALUES (%s,%s,%s,%s,%s) RETURNING decision_id",
                    (file_name, req.decision, req.reason, req.adjusted_total, actor))
                decision_id = cur.fetchone()[0]
                cur.execute(
                    "UPDATE pil_app.invoice_review_queue SET status=%s WHERE file_name=%s",
                    (req.decision, file_name))
                cur.execute(
                    "INSERT INTO pil_app.app_audit_log (actor, action, entity) "
                    "VALUES (%s,%s,%s)", (actor, f"invoice_{req.decision}", file_name))
            conn.commit()
            return {"decision_id": decision_id, "file_name": file_name,
                    "decision": req.decision}
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Lakebase decide failed: %s", exc)
            return self._decide_memory(file_name, req)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def _decide_memory(self, file_name: str, req: InvoiceDecisionRequest) -> dict[str, Any]:
        self._ensure_demo_seeded()
        demo_store.set_status(file_name, req.decision)
        return {"decision_id": None, "file_name": file_name, "decision": req.decision}


# A tiny static sample so the queue is never empty in a fresh demo.
_SAMPLE_QUEUE: list[dict[str, Any]] = [
    {"file_name": "invoice_0007.pdf", "invoice_no": "INV-2025-100007",
     "customer": "Meridian Electronics Pte Ltd", "extracted_total": 5231.0,
     "ground_truth_total": 4180.0, "exception_type": "total_mismatch"},
    {"file_name": "invoice_0013.pdf", "invoice_no": "INV-2025-100013",
     "customer": "Auburn Auto Parts Co.", "extracted_total": 2650.0,
     "ground_truth_total": 2650.0, "exception_type": "missing_po"},
    {"file_name": "invoice_0021.pdf", "invoice_no": "INV-2025-100007",
     "customer": "Zenith Machinery Ltd", "extracted_total": 3990.0,
     "ground_truth_total": 3990.0, "exception_type": "duplicate_no"},
]
