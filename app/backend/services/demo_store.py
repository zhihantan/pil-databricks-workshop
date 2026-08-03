"""Process-level in-memory store for the app's demo (no-Lakebase) mode.

Services are constructed per-request (FastAPI ``Depends``), so per-instance
state cannot persist across requests. When Lakebase is not connected, both the
invoice and inspection services route their writes here instead, so a decision
or work order made in one request is visible in the next — the app stays fully
clickable in a workshop before Lakebase is provisioned.

This is deliberately process-global and single-tenant; it is a *demo* fallback,
not production state (which lives in Lakebase). It is reset only on restart.
"""

from __future__ import annotations

import copy
import threading
from typing import Any

_LOCK = threading.Lock()

# Invoice review queue: file_name -> row dict. None until first seeded.
_QUEUE: dict[str, dict[str, Any]] | None = None
# Work orders created in demo mode.
_WORK_ORDERS: list[dict[str, Any]] = []


def is_seeded() -> bool:
    return _QUEUE is not None


def seed_queue(rows: list[dict[str, Any]]) -> None:
    """Seed the queue once (idempotent). Rows are deep-copied so callers'
    module-level sample lists are never mutated."""
    global _QUEUE
    with _LOCK:
        if _QUEUE is None:
            _QUEUE = {}
            for i, r in enumerate(rows):
                row = copy.deepcopy(r)
                row.setdefault("id", i + 1)
                row.setdefault("status", "pending")
                _QUEUE[row["file_name"]] = row


def list_queue(status: str | None = None) -> list[dict[str, Any]]:
    with _LOCK:
        rows = list((_QUEUE or {}).values())
    rows = [copy.deepcopy(r) for r in rows]
    if status:
        return [r for r in rows if r.get("status") == status]
    return rows


def set_status(file_name: str, status: str) -> None:
    with _LOCK:
        if _QUEUE is not None and file_name in _QUEUE:
            _QUEUE[file_name]["status"] = status


def upsert_queue_row(row: dict[str, Any]) -> None:
    """Insert or update one review-queue row keyed by file_name (idempotent)."""
    global _QUEUE
    with _LOCK:
        if _QUEUE is None:
            _QUEUE = {}
        rec = copy.deepcopy(row)
        rec.setdefault("status", "pending")
        rec["id"] = (
            _QUEUE[rec["file_name"]]["id"]
            if rec["file_name"] in _QUEUE
            else len(_QUEUE) + 1
        )
        _QUEUE[rec["file_name"]] = rec


def pending_count() -> int:
    with _LOCK:
        return sum(1 for r in (_QUEUE or {}).values() if r.get("status") == "pending")


def add_work_order(row: dict[str, Any]) -> int:
    with _LOCK:
        wo_id = len(_WORK_ORDERS) + 1
        rec = copy.deepcopy(row)
        rec["work_order_id"] = wo_id
        rec.setdefault("status", "open")
        _WORK_ORDERS.append(rec)
        return wo_id


def open_work_order_count() -> int:
    with _LOCK:
        return sum(1 for w in _WORK_ORDERS if w.get("status") == "open")


def reset() -> None:
    """Test helper: clear all demo state."""
    global _QUEUE, _WORK_ORDERS
    with _LOCK:
        _QUEUE = None
        _WORK_ORDERS = []
