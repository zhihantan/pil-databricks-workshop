"""Agent Bricks module helpers: the invoice-extraction JSON schema, the
gateway-governed ``ai_parse_document`` / ``ai_query`` fallback pipeline SQL, and
the container-vision classification prompt/schema.

All model calls resolve their endpoint through :mod:`pil_workshop.llm`, so both
notebook and app traffic hit the same Unity-AI-Gateway-governed FMAPI endpoints
and show up on dashboard Page 4. No endpoint name is hardcoded here.
"""

from __future__ import annotations

import json
from typing import Any

# ---------------------------------------------------------------------------
# Invoice extraction target schema (Information Extraction agent + fallback).
# ---------------------------------------------------------------------------
INVOICE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "invoice_no": {"type": "string"},
        "date": {"type": "string", "description": "ISO date the invoice was issued"},
        "customer": {"type": "string"},
        "po_number": {"type": ["string", "null"]},
        "currency": {"type": "string"},
        "line_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "description": {"type": "string"},
                    "amount": {"type": "number"},
                },
                "required": ["description", "amount"],
            },
        },
        "subtotal": {"type": "number"},
        "tax": {"type": "number"},
        "total": {"type": "number"},
        "payment_terms": {"type": ["string", "null"]},
    },
    "required": ["invoice_no", "date", "customer", "currency", "total"],
}

INVOICE_SYSTEM_PROMPT = (
    "You are a precise freight-invoice data extractor for a container shipping "
    "line. Extract the fields exactly as they appear. If a field is missing, "
    "return null for it — never invent values. Return only JSON matching the "
    "provided schema."
)


def invoice_extraction_prompt(document_text: str) -> str:
    """Build the user prompt for extracting one invoice's fields."""
    return (
        "Extract the invoice fields from the following freight invoice text and "
        "return JSON with keys: invoice_no, date, customer, po_number, currency, "
        "line_items (each with description, amount), subtotal, tax, total, "
        "payment_terms.\n\n"
        f"INVOICE TEXT:\n{document_text}"
    )


# ---------------------------------------------------------------------------
# Container-vision classification schema + prompt.
# ---------------------------------------------------------------------------
INSPECTION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "damage": {"type": "string", "enum": ["none", "minor", "major"]},
        "damage_type": {
            "type": "string",
            "enum": ["none", "dent", "rust", "door_misalignment", "other"],
        },
        "confidence": {"type": "number", "description": "0.0–1.0"},
        "recommended_action": {"type": "string"},
    },
    "required": ["damage", "damage_type", "confidence", "recommended_action"],
}

VISION_SYSTEM_PROMPT = (
    "You are a container-inspection assistant. Assess the shipping container in "
    "the image for structural damage. Classify overall damage as none/minor/"
    "major, identify the primary damage type, give a confidence 0-1, and "
    "recommend an action (e.g. 'release', 'flag for manual inspection', "
    "'remove from service'). Return only JSON matching the schema."
)

VISION_USER_PROMPT = (
    "Inspect this container image. Return JSON: damage (none|minor|major), "
    "damage_type, confidence (0-1), recommended_action."
)


# ---------------------------------------------------------------------------
# SQL builders — the always-works AI-function pipeline. Endpoint name is injected
# from pil_workshop.llm so it is never hardcoded.
#
# Design chosen from a live bake-off on the real invoices (scored vs. ground
# truth) — each function is used for what it is best at:
#   * ai_parse_document  — PDF -> structured text (the document-parsing step).
#   * ai_extract         — fast, robust FLAT header fields (invoice_no, customer,
#                          po_number, currency, total). Returns a clean struct;
#                          matched ai_query on accuracy and was ~25% faster.
#   * ai_query           — the NESTED schema ai_extract can't do: line_items[],
#                          subtotal, tax, total, payment_terms (JSON).
# Both AI-function calls hit the same Unity-AI-Gateway-governed FMAPI endpoint.
# ---------------------------------------------------------------------------
# Flat labels ai_extract pulls directly from the parsed text.
_EXTRACT_LABELS = ("invoice_no", "customer", "po_number", "currency", "total")


def build_invoice_parse_sql(catalog: str, invoices_volume_path: str) -> str:
    """Return SQL that parses invoice PDFs to readable text with ai_parse_document.

    Writes ``silver.invoice_parsed_text`` (file_name, text). Parsing once and
    reusing the text keeps the two extraction calls cheap and consistent.
    """
    return f"""
CREATE OR REPLACE TABLE `{catalog}`.`silver`.`invoice_parsed_text` AS
WITH parsed AS (
    SELECT
        regexp_extract(_metadata.file_path, '([^/]+)$', 1) AS file_name,
        ai_parse_document(content) AS doc
    FROM READ_FILES('{invoices_volume_path}', format => 'binaryFile')
)
SELECT
    file_name,
    array_join(
        transform(
            try_cast(doc:document:elements AS ARRAY<STRING>),
            x -> get_json_object(x, '$.content')
        ),
        '\\n'
    ) AS text
FROM parsed
-- error_status is a JSON null -> the string 'null' when path-extracted, so a
-- SQL "IS NULL" check drops everything; keep rows that actually parsed elements.
WHERE size(try_cast(doc:document:elements AS ARRAY<STRING>)) > 0
""".strip()


def build_invoice_extraction_sql(
    catalog: str, text_endpoint: str, invoices_volume_path: str
) -> str:
    """Return SQL that extracts structured invoice data → ``silver.invoice_extractions``.

    Assumes ``silver.invoice_parsed_text`` exists (see :func:`build_invoice_parse_sql`).
    Combines ``ai_extract`` (flat header fields) with ``ai_query`` (nested
    line-items schema). ``invoices_volume_path`` is unused here but kept for
    signature stability.
    """
    labels = ", ".join(f"'{lbl}'" for lbl in _EXTRACT_LABELS)
    return f"""
CREATE OR REPLACE TABLE `{catalog}`.`silver`.`invoice_extractions` AS
WITH flat AS (
    -- ai_extract: fast, robust flat header fields (returns a struct).
    SELECT file_name,
           ai_extract(text, array({labels})) AS f
    FROM `{catalog}`.`silver`.`invoice_parsed_text`
),
nested AS (
    -- ai_query: the nested schema (line_items, subtotal, tax) ai_extract can't do.
    SELECT file_name,
           ai_query(
               '{text_endpoint}',
               CONCAT(
                   'Extract this freight invoice as JSON with keys: invoice_no, ',
                   'date, customer, po_number, currency, line_items (array of ',
                   'objects with description and amount), subtotal, tax, total, ',
                   'payment_terms. Return ONLY JSON.\\n\\nTEXT:\\n', text)
           ) AS raw_json
    FROM `{catalog}`.`silver`.`invoice_parsed_text`
),
nested_clean AS (
    -- Strip any prose/code-fence around the JSON body.
    SELECT file_name,
           regexp_extract(raw_json, '(\\\\{{(?s).*\\\\}})', 1) AS extraction_json
    FROM nested
)
SELECT
    fl.file_name,
    nc.extraction_json,
    fl.f.invoice_no                                       AS invoice_no,
    NULLIF(fl.f.po_number, '')                            AS po_number,
    fl.f.customer                                         AS customer,
    fl.f.currency                                         AS currency,
    -- prefer the nested numeric total; fall back to ai_extract's flat total
    COALESCE(
        CAST(get_json_object(nc.extraction_json, '$.subtotal') AS DOUBLE), 0
    )                                                     AS subtotal,
    CAST(get_json_object(nc.extraction_json, '$.tax')   AS DOUBLE) AS tax,
    COALESCE(
        CAST(get_json_object(nc.extraction_json, '$.total') AS DOUBLE),
        CAST(regexp_extract(fl.f.total, '([0-9]+\\\\.?[0-9]*)', 1) AS DOUBLE)
    )                                                     AS total
FROM flat fl
JOIN nested_clean nc ON fl.file_name = nc.file_name
""".strip()


def build_invoice_reconciliation_sql(catalog: str) -> str:
    """Return SQL that reconciles extractions vs ground truth → exceptions.

    Flags total mismatches, missing POs, and duplicate invoice numbers into
    ``gold.invoice_exceptions``.
    """
    return f"""
CREATE OR REPLACE TABLE `{catalog}`.`gold`.`invoice_exceptions` AS
WITH x AS (
    SELECT e.*, g.total AS gt_total, g.po_number AS gt_po, g.gt_anomaly
    FROM `{catalog}`.`silver`.`invoice_extractions` e
    LEFT JOIN `{catalog}`.`silver`.`invoice_pdf_ground_truth` g
      ON e.file_name = g.file_name
),
dups AS (
    SELECT invoice_no FROM `{catalog}`.`silver`.`invoice_extractions`
    WHERE invoice_no IS NOT NULL
    GROUP BY invoice_no HAVING COUNT(*) > 1
)
SELECT
    x.file_name, x.invoice_no, x.customer, x.total, x.gt_total,
    CASE
        WHEN x.po_number IS NULL THEN 'missing_po'
        WHEN x.gt_total IS NOT NULL AND ABS(COALESCE(x.total,0) - x.gt_total) > 1.0
            THEN 'total_mismatch'
        WHEN d.invoice_no IS NOT NULL THEN 'duplicate_no'
        ELSE NULL
    END AS exception_type,
    x.gt_anomaly AS ground_truth_anomaly
FROM x
LEFT JOIN dups d ON x.invoice_no = d.invoice_no
WHERE
    x.po_number IS NULL
    OR (x.gt_total IS NOT NULL AND ABS(COALESCE(x.total,0) - x.gt_total) > 1.0)
    OR d.invoice_no IS NOT NULL
""".strip()


def build_container_vision_sql(
    catalog: str, vision_endpoint: str, images_volume_path: str
) -> str:
    """Return SQL that classifies container images via the vision endpoint.

    Reads images as base64 and calls ``ai_query`` against the governed
    multimodal endpoint, writing to ``silver.container_inspections``.
    """
    schema_json = json.dumps(INSPECTION_SCHEMA)
    return f"""
CREATE OR REPLACE TABLE `{catalog}`.`silver`.`container_inspections` AS
WITH imgs AS (
    SELECT
        regexp_extract(_metadata.file_path, '([^/]+)$', 1) AS file_name,
        base64(content) AS b64
    FROM READ_FILES('{images_volume_path}', format => 'binaryFile')
)
SELECT
    file_name,
    ai_query(
        '{vision_endpoint}',
        CONCAT('{VISION_USER_PROMPT}'),
        files => array(b64),
        responseFormat => '{{"type":"json_schema","json_schema":'
            || '{{"name":"inspection","schema":{schema_json}}}}}'
    ) AS inspection_json
FROM imgs
""".strip()


def build_vision_scored_sql(catalog: str) -> str:
    """Return SQL joining inspections to labels and computing correctness.

    ``container_inspections`` is written by ``classify_container_images`` with
    already-parsed columns (damage/damage_type/confidence/recommended_action),
    so this reads them directly (no JSON extraction).
    """
    return f"""
CREATE OR REPLACE TABLE `{catalog}`.`silver`.`container_inspections_scored` AS
SELECT
    i.file_name,
    l.container_no,
    i.damage              AS pred_damage,
    i.damage_type         AS pred_damage_type,
    i.confidence          AS confidence,
    i.recommended_action  AS recommended_action,
    l.gt_damage,
    l.gt_damage_type,
    CASE WHEN i.damage = l.gt_damage THEN 1 ELSE 0 END AS is_correct
FROM `{catalog}`.`silver`.`container_inspections` i
JOIN `{catalog}`.`silver`.`container_image_labels` l
  ON i.file_name = l.file_name
""".strip()


# ---------------------------------------------------------------------------
# Python-based container vision (reliable path).
#
# Passing images to a multimodal model via SQL ``ai_query(..., files => ...)``
# is version/runtime-sensitive (the argument typing varies and errors with a
# DATATYPE_MISMATCH on some serverless runtimes). The robust path — identical to
# how the app backend does it — is to call the governed endpoint with an
# OpenAI-style multimodal message per image through ``pil_workshop.llm.chat``.
# ---------------------------------------------------------------------------
def classify_container_images(
    image_dir: str,
    vision_endpoint: str,
    llm_module: Any,
    *,
    limit: int | None = None,
    file_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Classify every ``*.png`` in ``image_dir`` via the governed vision endpoint.

    ``image_dir`` is a local/FUSE path to the images Volume (e.g.
    ``/Volumes/<cat>/bronze/container_images``). Returns one dict per image with
    the parsed inspection fields (``file_name`` + INSPECTION_SCHEMA keys),
    tolerant of a model response that isn't clean JSON.

    ``file_names`` optionally provides the list of image file names to read
    (e.g. from ``dbutils.fs.ls``); this avoids ``glob`` on the Volume FUSE mount,
    which is unreliable on serverless. When omitted, falls back to globbing.
    """
    import base64
    import json as _json
    import os

    if file_names is not None:
        files = [os.path.join(image_dir, n) for n in sorted(file_names)
                 if n.endswith(".png")]
    else:
        import glob
        files = sorted(glob.glob(os.path.join(image_dir, "*.png")))
    if limit:
        files = files[:limit]
    rows: list[dict[str, Any]] = []
    for path in files:
        with open(path, "rb") as fh:
            b64 = base64.b64encode(fh.read()).decode()
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
        row: dict[str, Any] = {
            "file_name": os.path.basename(path),
            "damage": None, "damage_type": None,
            "confidence": None, "recommended_action": None,
        }
        try:
            raw = llm_module.chat(
                messages, endpoint=vision_endpoint, max_tokens=300,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "inspection", "schema": INSPECTION_SCHEMA},
                },
            )
            parsed = _json.loads(raw)
            row.update({k: parsed.get(k) for k in
                        ("damage", "damage_type", "confidence", "recommended_action")})
        except Exception:  # noqa: BLE001 - keep a row even if one image fails
            row["damage"] = "unknown"
        rows.append(row)
    return rows
