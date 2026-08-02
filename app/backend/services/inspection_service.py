"""Container inspection service: list inspections, run/refresh analysis via the
governed vision endpoint, and create work orders.

Analysis reads from ``silver.container_inspections_scored`` when present. A
single-image "refresh" calls the governed multimodal endpoint through
``pil_workshop.llm`` so app traffic lands on Dashboard Page 4.
"""

from __future__ import annotations

import base64
import json
from typing import Any

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.models.schemas import InspectionItem, WorkOrderRequest
from backend.services import demo_store

LOG = get_logger("backend.inspection_service")

_VALID_DAMAGE = {"none", "minor", "major"}


class InspectionService:
    def __init__(self, conn_factory: Any = None, sql_fn: Any = None) -> None:
        self._conn_factory = conn_factory
        self._sql_fn = sql_fn

    def list_inspections(self) -> list[InspectionItem]:
        settings = get_settings()
        rows: list[dict[str, Any]] = []
        if self._sql_fn:
            try:
                rows = list(self._sql_fn(
                    f"SELECT file_name, container_no, pred_damage AS damage, "
                    f"pred_damage_type AS damage_type, confidence, recommended_action "
                    f"FROM {settings.silver}.container_inspections_scored LIMIT 200"))
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Inspection read failed: %s", exc)
        if not rows:
            rows = _SAMPLE_INSPECTIONS
        items = []
        for r in rows:
            # Guard the strict damage enum: model/parse drift could yield an
            # unexpected label, which would 500 the whole endpoint otherwise.
            row = dict(r)
            if row.get("damage") not in _VALID_DAMAGE:
                row["damage"] = None
            item = InspectionItem(**row)
            item.image_url = f"/api/inspections/{item.file_name}/image"
            items.append(item)
        return items

    def refresh_one(self, image_bytes: bytes, endpoint: str,
                    llm_module: Any) -> dict[str, Any]:
        """Classify a single image via the governed vision endpoint.

        ``llm_module`` is injected (``pil_workshop.llm``) so tests can mock the
        model call. Returns the parsed inspection dict.
        """
        from pil_workshop.agent_bricks import (
            INSPECTION_SCHEMA,
            VISION_SYSTEM_PROMPT,
            VISION_USER_PROMPT,
        )

        b64 = base64.b64encode(image_bytes).decode()
        messages = [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_USER_PROMPT},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            },
        ]
        raw = llm_module.chat(
            messages,
            endpoint=endpoint,
            max_tokens=300,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "inspection", "schema": INSPECTION_SCHEMA},
            },
        )
        try:
            return json.loads(raw)
        except (ValueError, TypeError):
            return {"damage": "unknown", "raw": raw}

    def create_work_order(self, req: WorkOrderRequest, actor: str) -> dict[str, Any]:
        conn = self._conn_factory() if self._conn_factory else None
        if conn is None:
            # Demo mode: persist to the process-level store so the open
            # work-order count on Home reflects it across requests.
            wo_id = demo_store.add_work_order({
                "file_name": req.file_name, "container_no": req.container_no,
                "damage": req.damage, "damage_type": req.damage_type,
                "action": req.action,
            })
            return {"work_order_id": wo_id, "file_name": req.file_name,
                    "status": "open",
                    "note": "created (demo/in-memory; Lakebase not connected)"}
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO pil_app.inspection_work_orders "
                    "(file_name, container_no, damage, damage_type, action, created_by) "
                    "VALUES (%s,%s,%s,%s,%s,%s) RETURNING work_order_id",
                    (req.file_name, req.container_no, req.damage, req.damage_type,
                     req.action, actor))
                wo_id = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO pil_app.app_audit_log (actor, action, entity) "
                    "VALUES (%s,'work_order_created',%s)", (actor, req.file_name))
            conn.commit()
            return {"work_order_id": wo_id, "file_name": req.file_name, "status": "open"}
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Work order create failed: %s", exc)
            return {"work_order_id": None, "file_name": req.file_name, "status": "error"}
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass


_SAMPLE_INSPECTIONS: list[dict[str, Any]] = [
    {"file_name": "container_0001.png", "container_no": "PILU1234561",
     "damage": "none", "damage_type": "none", "confidence": 0.97,
     "recommended_action": "Release"},
    {"file_name": "container_0002.png", "container_no": "PILU7654320",
     "damage": "minor", "damage_type": "rust", "confidence": 0.82,
     "recommended_action": "Flag for manual inspection"},
    {"file_name": "container_0003.png", "container_no": "PILU5551239",
     "damage": "major", "damage_type": "dent", "confidence": 0.91,
     "recommended_action": "Remove from service"},
]
