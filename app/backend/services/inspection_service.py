"""Container inspection service: list inspections, run/refresh analysis via the
governed vision endpoint, and create work orders.

Analysis reads from ``silver.container_inspections_scored`` when present. A
single-image "refresh" calls the governed multimodal endpoint through
``pil_workshop.llm`` so app traffic lands on Dashboard Page 4.
"""

from __future__ import annotations

import json
from typing import Any

from backend.core.config import get_settings
from backend.core.logging import get_logger
from backend.models.schemas import InspectionItem, WorkOrderRequest
from backend.services import demo_store

LOG = get_logger("backend.inspection_service")

_VALID_DAMAGE = {"none", "minor", "major"}


# Token/cost estimation for the vision call (ai_query exposes no inline usage).
# An image costs a large-ish fixed input-token block; output is small JSON.
_VISION_IMAGE_TOKENS = 1200  # rough fixed cost of one container image
_CHARS_PER_TOKEN = 4.0
_COST_PER_M_TOKENS = 5.0  # same blended $/1M as the gold usage views


class InspectionService:
    def __init__(
        self,
        conn_factory: Any = None,
        sql_fn: Any = None,
        workspace_client: Any = None,
        write_fn: Any = None,
    ) -> None:
        self._conn_factory = conn_factory
        self._sql_fn = sql_fn
        self._wc = workspace_client
        # write_fn persists each live upload to the Delta sink (best-effort);
        # when None the persist step is skipped (analysis still returns).
        self._write_fn = write_fn

    def list_inspections(self) -> list[InspectionItem]:
        settings = get_settings()
        rows: list[dict[str, Any]] = []
        if self._sql_fn:
            try:
                rows = list(self._sql_fn(
                    f"SELECT file_name, container_no, pred_damage AS damage, "
                    f"pred_damage_type AS damage_type, confidence, recommended_action, "
                    f"gt_damage, is_correct "
                    f"FROM {settings.silver}.container_inspections_scored LIMIT 200"))
            except Exception as exc:  # noqa: BLE001
                LOG.warning("Inspection read failed: %s", exc)
        # Live uploads from the app's Delta sink. Read separately (not a SQL
        # UNION) so a missing/empty sink on a fresh clone can never blank the
        # batch gallery. Uploads are listed first and win on file-name dedup so
        # a re-analyzed image shows its latest result exactly once.
        uploads = self._read_uploads()
        if not rows and not uploads:
            rows = _SAMPLE_INSPECTIONS
        items = []
        seen: set[str] = set()
        allowed = set(InspectionItem.model_fields)
        for r in (*uploads, *rows):
            # Guard the strict damage enum: model/parse drift could yield an
            # unexpected label, which would 500 the whole endpoint otherwise.
            row = {k: v for k, v in dict(r).items() if k in allowed}
            fn = row.get("file_name")
            if fn in seen:
                continue
            seen.add(fn)
            if row.get("damage") not in _VALID_DAMAGE:
                row["damage"] = None
            if "is_correct" in row and row["is_correct"] is not None:
                row["is_correct"] = bool(row["is_correct"])
            item = InspectionItem(**row)
            item.image_url = f"/api/inspections/{item.file_name}/image"
            items.append(item)
        return items

    def _read_uploads(self) -> list[dict[str, Any]]:
        """Return app-uploaded inspections from the Delta sink (latest per file).

        Graceful: ``[]`` when there's no SQL fn or the sink isn't created yet
        (``self._sql_fn`` is the graceful reader, so a missing table yields []).
        Uploads carry no ground-truth, so ``gt_damage``/``is_correct`` are absent
        and default to ``None`` on the model.
        """
        if not self._sql_fn:
            return []
        from pil_workshop.agent_bricks import container_uploads_table

        table = container_uploads_table(get_settings().catalog)
        sql = f"""
            WITH ranked AS (
                SELECT file_name, damage, damage_type, confidence, recommended_action,
                       ROW_NUMBER() OVER (
                           PARTITION BY file_name ORDER BY analyzed_at DESC
                       ) AS rn
                FROM {table}
            )
            SELECT file_name, damage, damage_type, confidence, recommended_action
            FROM ranked WHERE rn = 1 ORDER BY file_name
        """
        try:
            return list(self._sql_fn(sql))
        except Exception as exc:  # noqa: BLE001
            LOG.info("Container uploads read unavailable: %s", exc)
            return []

    def accuracy_summary(self) -> dict[str, Any]:
        """Return the vision agent's accuracy vs labelled ground truth.

        Reads the scored table's is_correct + pred/gt damage. Returns
        scored/correct/accuracy_pct plus off-diagonal confusion counts
        ("predicted→actual" -> n). Empty summary if no ground truth is present.
        """
        settings = get_settings()
        if not self._sql_fn:
            return {"scored": 0, "correct": 0, "accuracy_pct": None, "confusions": {}}
        try:
            rows = list(self._sql_fn(
                f"SELECT pred_damage, gt_damage, COUNT(*) AS n "
                f"FROM {settings.silver}.container_inspections_scored "
                f"WHERE gt_damage IS NOT NULL GROUP BY pred_damage, gt_damage"))
        except Exception as exc:  # noqa: BLE001
            LOG.info("Accuracy read unavailable: %s", exc)
            return {"scored": 0, "correct": 0, "accuracy_pct": None, "confusions": {}}
        scored = correct = 0
        confusions: dict[str, int] = {}
        for r in rows:
            pred, gt, n = r.get("pred_damage"), r.get("gt_damage"), int(r.get("n") or 0)
            scored += n
            if pred == gt:
                correct += n
            else:
                confusions[f"{pred}→{gt}"] = confusions.get(f"{pred}→{gt}", 0) + n
        acc = round(100.0 * correct / scored, 1) if scored else None
        return {"scored": scored, "correct": correct, "accuracy_pct": acc,
                "confusions": confusions}

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
            image_data_url,
        )

        # Sniff the media type from the bytes — the endpoint rejects a declared
        # type that disagrees with the content (e.g. a real JPEG upload).
        data_url = image_data_url(image_bytes)
        messages = [
            {"role": "system", "content": VISION_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": VISION_USER_PROMPT},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ]
        raw = llm_module.chat(
            messages,
            endpoint=endpoint,
            # Headroom for the verbose `reasoning` field + the classification
            # fields that follow it; 300 truncated the JSON before confidence/
            # recommended_action on longer real-photo descriptions.
            max_tokens=600,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "inspection", "schema": INSPECTION_SCHEMA},
            },
        )
        return _parse_inspection(raw)

    def save_and_analyze(
        self, file_name: str, content: bytes, endpoint: str, llm_module: Any
    ) -> dict[str, Any]:
        """Upload a container image to the volume, analyze it, return result + metrics.

        Mirrors the invoice upload flow: save to bronze/container_images via the
        Files API (Volumes aren't fs-mounted in Apps), run the governed vision
        endpoint, and attach run metrics (duration + estimated tokens/cost).
        """
        import io
        import os
        import time

        settings = get_settings()
        safe = os.path.basename(file_name)
        vol_path = f"/Volumes/{settings.catalog}/bronze/container_images/{safe}"

        t0 = time.perf_counter()
        if self._wc is not None:
            self._wc.files.upload(vol_path, io.BytesIO(content), overwrite=True)
        save_ms = int((time.perf_counter() - t0) * 1000)

        t1 = time.perf_counter()
        result = self.refresh_one(content, endpoint, llm_module)
        analyze_ms = int((time.perf_counter() - t1) * 1000)

        raw_out = json.dumps(result)
        est_in = _VISION_IMAGE_TOKENS
        est_out = max(1, round(len(raw_out) / _CHARS_PER_TOKEN))
        est_total = est_in + est_out
        analysis = {
            "file_name": safe,
            "volume_path": vol_path,
            "image_url": f"/api/inspections/{safe}/image",
            "damage": result.get("damage"),
            "damage_type": result.get("damage_type"),
            "confidence": _to_float(result.get("confidence")),
            "recommended_action": result.get("recommended_action"),
            "metrics": {
                "save_ms": save_ms,
                "analyze_ms": analyze_ms,
                "duration_ms": save_ms + analyze_ms,
                "est_input_tokens": est_in,
                "est_output_tokens": est_out,
                "est_total_tokens": est_total,
                "est_cost_usd": round(est_total / 1_000_000 * _COST_PER_M_TOKENS, 4),
                "model_endpoint": endpoint,
            },
        }
        # Persist to the Delta sink so the upload appears in the gallery and
        # survives the daily batch rebuild. Best-effort: a write failure never
        # fails the analysis response (the result is already computed).
        try:
            self._persist(analysis, result, endpoint, est_total)
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Persist to container sink failed (analysis still returned): %s", exc)
        return analysis

    def _persist(
        self, analysis: dict[str, Any], raw_result: dict[str, Any],
        endpoint: str | None, est_total: int,
    ) -> None:
        """Write one upload row to the container Delta sink (parameterized INSERT).

        Skipped when no ``write_fn`` is wired. ``CONTAINER_UPLOAD_COLUMNS`` is
        the shared source of truth for column order, so DDL and INSERT can't
        drift. ``analyzed_at`` is defaulted by the table, not passed here.
        """
        if not self._write_fn:
            return
        from pil_workshop.agent_bricks import (
            CONTAINER_UPLOAD_COLUMNS,
            container_uploads_table,
        )

        table = container_uploads_table(get_settings().catalog)
        values = {
            "file_name": analysis.get("file_name"),
            "volume_path": analysis.get("volume_path"),
            "damage": analysis.get("damage"),
            "damage_type": analysis.get("damage_type"),
            "confidence": analysis.get("confidence"),
            "recommended_action": analysis.get("recommended_action"),
            "model_endpoint": endpoint,
            "est_total_tokens": est_total,
            "raw_json": json.dumps(raw_result, default=str),
        }
        col_names = [c for c, _ in CONTAINER_UPLOAD_COLUMNS]
        sql = (
            f"INSERT INTO {table} ({', '.join(f'`{c}`' for c in col_names)}) "
            f"VALUES ({', '.join(f':{c}' for c in col_names)})"
        )
        self._write_fn(sql, {c: values.get(c) for c in col_names})

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


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_inspection(raw: str) -> dict[str, Any]:
    """Parse the vision model's JSON response, resiliently.

    A long `reasoning` field can occasionally make the response hit the token
    cap and truncate mid-JSON. Rather than surface null fields (or crash), try:
      1. strict JSON parse (the normal case);
      2. salvage the `damage`/`damage_type`/`recommended_action` values from a
         truncated/partial object via regex;
      3. fall back to a SAFE default — flag for manual inspection at low
         confidence — so an unparseable response never silently "clears" a
         container or blanks the UI.
    """
    import re

    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and obj.get("damage") in _VALID_DAMAGE:
            return obj
    except (ValueError, TypeError):
        obj = None

    salvaged: dict[str, Any] = {}
    if raw:
        m = re.search(r'"damage"\s*:\s*"(none|minor|major)"', raw)
        if m:
            salvaged["damage"] = m.group(1)
        m = re.search(r'"damage_type"\s*:\s*"([^"]+)"', raw)
        if m:
            salvaged["damage_type"] = m.group(1)
        m = re.search(r'"confidence"\s*:\s*([0-9.]+)', raw)
        if m:
            salvaged["confidence"] = _to_float(m.group(1))
        m = re.search(r'"recommended_action"\s*:\s*"([^"]+)"', raw)
        if m:
            salvaged["recommended_action"] = m.group(1)
    if salvaged.get("damage") in _VALID_DAMAGE:
        return salvaged

    # Nothing usable — never clear or blank. Flag for a human.
    return {
        "damage": "none",
        "damage_type": "other",
        "confidence": 0.3,
        "recommended_action": "Flag for manual inspection — analysis inconclusive",
    }


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
