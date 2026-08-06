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
            cols_sql = (
                "id, file_name, invoice_no, customer, po_number, currency, "
                "extracted_total, ground_truth_total, exception_type, status"
            )
            with conn.cursor() as cur:
                if status:
                    cur.execute(
                        f"SELECT {cols_sql} FROM pil_app.invoice_review_queue "
                        "WHERE status=%s ORDER BY id", (status,))
                else:
                    cur.execute(
                        f"SELECT {cols_sql} FROM pil_app.invoice_review_queue ORDER BY id")
                cols = [c[0] for c in cur.description]
                rows = [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
                # A completely UNSEEDED Lakebase queue (0 rows total — e.g. 09
                # couldn't read gold.invoice_exceptions) should still show the
                # real flagged invoices, not a blank screen. Distinguish "never
                # seeded" from "seeded but all decided" by checking the total
                # count (ignoring the status filter): only seed the demo view
                # when the table is genuinely empty, so decisions still clear
                # the queue once real rows exist.
                if not rows and self._table_is_empty(cur):
                    LOG.info("Lakebase queue unseeded — seeding demo view from UC.")
                    return self._list_from_memory(status)
            return rows
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Lakebase queue read failed, using memory: %s", exc)
            return self._list_from_memory(status)
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    @staticmethod
    def _table_is_empty(cur: Any) -> bool:
        """True if the review queue has NO rows at all (never seeded), as opposed
        to being empty only for the current status filter (all decided)."""
        try:
            cur.execute("SELECT 1 FROM pil_app.invoice_review_queue LIMIT 1")
            return cur.fetchone() is None
        except Exception:  # noqa: BLE001
            return False

    def _ensure_demo_seeded(self) -> None:
        """Seed the process-level demo store once, preferring REAL data so the
        queue's fields always match the PDF each row previews.

        Source priority (each row's ``file_name`` must correspond to a real PDF
        in the Volume, so its customer/invoice_no agree with the rendered doc):
          1. ``gold.invoice_exceptions`` — the flagged-invoice table (best: it
             already carries the derived exception_type + ground-truth total).
          2. ``silver.invoice_pdf_ground_truth`` — per-file truth written by
             notebook 07; used when 08's extraction/reconciliation hasn't run
             (or failed), so the queue still matches the actual documents.
          3. ``_SAMPLE_QUEUE`` — a tiny static last resort (values kept in sync
             with the seed=42 generator) only when no UC tables are reachable.

        Using the shared demo_store (not per-instance state) means a decision
        made in one request is visible in the next — services are constructed
        fresh per request, so instance state would otherwise be lost.
        """
        if demo_store.is_seeded():
            return
        rows = self._seed_rows_from_uc() if self._sql_fn else []
        demo_store.seed_queue(rows or _SAMPLE_QUEUE)

    def _seed_rows_from_uc(self) -> list[dict[str, Any]]:
        """Read demo-queue rows from UC (exceptions table, else ground truth).

        Returns [] if neither table is reachable so the caller falls back to the
        static sample. Both queries are best-effort and never raise.
        """
        settings = get_settings()
        # 1. The flagged-exceptions table (already scoped to problem invoices).
        try:
            rows = list(self._sql_fn(
                f"SELECT file_name, invoice_no, customer, total AS extracted_total, "
                f"gt_total AS ground_truth_total, exception_type "
                f"FROM {settings.gold}.invoice_exceptions LIMIT 200"))
            if rows:
                return rows
        except Exception as exc:  # noqa: BLE001
            LOG.warning("UC seed from gold.invoice_exceptions failed: %s", exc)
        # 2. Per-file ground truth (matches the PDFs by construction). Keep only
        #    the flagged ones so the "review queue" stays a queue of exceptions.
        try:
            rows = list(self._sql_fn(
                f"SELECT file_name, invoice_no, customer, "
                f"total AS extracted_total, total AS ground_truth_total, "
                f"gt_anomaly AS exception_type "
                f"FROM {settings.silver}.invoice_pdf_ground_truth "
                f"WHERE gt_anomaly IS NOT NULL LIMIT 200"))
            if rows:
                return rows
        except Exception as exc:  # noqa: BLE001
            LOG.warning("UC seed from silver.invoice_pdf_ground_truth failed: %s", exc)
        return []

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
        corr = _clean_corrections(req)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pil_app.invoice_decisions "
                    "(file_name, decision, reason, adjusted_total, decided_by) "
                    "VALUES (%s,%s,%s,%s,%s) RETURNING decision_id",
                    (file_name, req.decision, _reason_with_corrections(req, corr),
                     req.adjusted_total, actor))
                decision_id = cur.fetchone()[0]
                # Apply reviewer corrections to the queue row, then set status.
                set_cols = ["status=%s"]
                params: list[Any] = [req.decision]
                for col, val in corr.items():
                    set_cols.append(f"{col}=%s")
                    params.append(val)
                params.append(file_name)
                cur.execute(
                    f"UPDATE pil_app.invoice_review_queue SET {', '.join(set_cols)} "
                    "WHERE file_name=%s", tuple(params))
                cur.execute(
                    "INSERT INTO pil_app.app_audit_log (actor, action, entity) "
                    "VALUES (%s,%s,%s)", (actor, f"invoice_{req.decision}", file_name))
            conn.commit()
            return {"decision_id": decision_id, "file_name": file_name,
                    "decision": req.decision, "corrections_applied": corr}
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
        corr = _clean_corrections(req)
        if corr:
            demo_store.apply_corrections(file_name, corr)
        demo_store.set_status(file_name, req.decision)
        return {"decision_id": None, "file_name": file_name,
                "decision": req.decision, "corrections_applied": corr}

    # ---- enqueue (from the upload flow) ---------------------------------
    def enqueue_for_review(
        self,
        file_name: str,
        invoice_no: str | None,
        customer: str | None,
        extracted_total: float | None,
        exception_type: str | None,
        po_number: str | None = None,
        currency: str | None = None,
    ) -> str:
        """Add a flagged extraction to the review queue (Lakebase, else demo).

        Upsert keyed by ``file_name`` (UNIQUE) so re-uploading the same invoice
        updates its row instead of duplicating. Best-effort: returns the backing
        store used ("lakebase" | "memory"); callers should not fail on errors.
        """
        conn = self._conn_factory() if self._conn_factory else None
        if conn is not None:
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "INSERT INTO pil_app.invoice_review_queue "
                        "(file_name, invoice_no, customer, po_number, currency, "
                        " extracted_total, exception_type, status) "
                        "VALUES (%s,%s,%s,%s,%s,%s,%s,'pending') "
                        "ON CONFLICT (file_name) DO UPDATE SET "
                        "  invoice_no=EXCLUDED.invoice_no, "
                        "  customer=EXCLUDED.customer, "
                        "  po_number=EXCLUDED.po_number, "
                        "  currency=EXCLUDED.currency, "
                        "  extracted_total=EXCLUDED.extracted_total, "
                        "  exception_type=EXCLUDED.exception_type, "
                        "  status='pending'",
                        (file_name, invoice_no, customer, po_number, currency,
                         extracted_total, exception_type),
                    )
                    cur.execute(
                        "INSERT INTO pil_app.app_audit_log (actor, action, entity) "
                        "VALUES (%s,%s,%s)",
                        ("app", f"invoice_enqueued_{exception_type or 'flagged'}", file_name),
                    )
                conn.commit()
                return "lakebase"
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Lakebase enqueue failed, using memory: %s", exc)
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        self._ensure_demo_seeded()
        demo_store.upsert_queue_row({
            "file_name": file_name,
            "invoice_no": invoice_no,
            "customer": customer,
            "po_number": po_number,
            "currency": currency,
            "extracted_total": extracted_total,
            "ground_truth_total": None,
            "exception_type": exception_type,
        })
        return "memory"


# A tiny static sample so the queue is never empty in a fresh demo (before UC /
# Lakebase are populated). These MUST correspond to the actual PDFs the same
# rows preview — otherwise the extracted-fields panel and the rendered document
# disagree. The values below are the real, deterministic output of the seed=42
# invoice generator (pil_workshop.datagen.unstructured.generate_invoice_pdfs,
# anchored to a fixed date so they never drift) for three flagged invoices, one
# per exception type. Regenerate with that function if the generator changes.
_SAMPLE_QUEUE: list[dict[str, Any]] = [
    # total_mismatch — total inflated vs subtotal+tax (invoice_0058.pdf).
    {"file_name": "invoice_0058.pdf", "invoice_no": "INV-2026-100058",
     "customer": "Kirin Foods Trading", "po_number": "PO200058", "currency": "CNY",
     "extracted_total": 2490.40, "ground_truth_total": 2490.40,
     "exception_type": "total_mismatch"},
    # missing_po — no PO number on the document (invoice_0001.pdf).
    {"file_name": "invoice_0001.pdf", "invoice_no": "INV-2024-100001",
     "customer": "Kirin Foods Trading", "po_number": None, "currency": "USD",
     "extracted_total": 7887.81, "ground_truth_total": 7887.81,
     "exception_type": "missing_po"},
    # duplicate_no — invoice_no reused from an earlier invoice (invoice_0015.pdf).
    {"file_name": "invoice_0015.pdf", "invoice_no": "INV-2026-100002",
     "customer": "Harbor Furniture Works", "po_number": "PO200015", "currency": "USD",
     "extracted_total": 9900.27, "ground_truth_total": 9900.27,
     "exception_type": "duplicate_no"},
]


# Reviewer-editable queue columns → their DB column names. Whitelisted so a
# corrections payload can never touch anything but these (no SQL injection via
# column names; values are always parameterized).
_CORRECTABLE = {
    "po_number": "po_number",
    "currency": "currency",
    "invoice_no": "invoice_no",
    "customer": "customer",
    "total": "extracted_total",  # UI calls it "total"; queue stores extracted_total
}


def _clean_corrections(req: InvoiceDecisionRequest) -> dict[str, Any]:
    """Whitelist + normalize the corrections payload to {db_column: value}."""
    out: dict[str, Any] = {}
    for key, val in (req.corrections or {}).items():
        col = _CORRECTABLE.get(key)
        if col is None:
            continue
        if isinstance(val, str):
            val = val.strip()
            if val == "":
                continue
        if val is None:
            continue
        if col == "extracted_total":
            try:
                val = float(val)
            except (TypeError, ValueError):
                continue
        out[col] = val
    # An explicit adjusted_total (legacy field) also corrects the total.
    if req.adjusted_total is not None:
        out["extracted_total"] = float(req.adjusted_total)
    return out


def _reason_with_corrections(req: InvoiceDecisionRequest, corr: dict[str, Any]) -> str | None:
    """Append a human-readable note of what was corrected to the decision reason."""
    parts = [req.reason] if req.reason else []
    if corr:
        parts.append("corrected: " + ", ".join(f"{k}={v}" for k, v in corr.items()))
    return " · ".join(parts) if parts else None
