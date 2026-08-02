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
# SQL builders — the fallback that always works. Endpoint name is injected from
# pil_workshop.llm so it is never hardcoded.
# ---------------------------------------------------------------------------
def build_invoice_extraction_sql(
    catalog: str, text_endpoint: str, invoices_volume_path: str
) -> str:
    """Return SQL that parses invoice PDFs and extracts structured fields.

    Uses ``ai_parse_document`` to OCR/parse each PDF in the Volume, then
    ``ai_query`` against the governed text endpoint with a JSON response schema,
    writing to ``silver.invoice_extractions``.
    """
    schema_json = json.dumps(INVOICE_SCHEMA)
    return f"""
-- Parse the raw PDFs in the Volume into text, then extract fields via the
-- gateway-governed FMAPI text endpoint ({text_endpoint}).
CREATE OR REPLACE TABLE `{catalog}`.`silver`.`invoice_extractions` AS
WITH parsed AS (
    SELECT
        _metadata.file_path AS file_path,
        regexp_extract(_metadata.file_path, '([^/]+)$', 1) AS file_name,
        ai_parse_document(content) AS parsed
    FROM READ_FILES('{invoices_volume_path}', format => 'binaryFile')
),
extracted AS (
    SELECT
        file_name,
        try_cast(
            ai_query(
                '{text_endpoint}',
                CONCAT(
                    'Extract invoice fields as JSON (invoice_no, date, customer, ',
                    'po_number, currency, line_items[desc,amount], subtotal, tax, ',
                    'total, payment_terms). Return only JSON.\\n\\nTEXT:\\n',
                    CAST(parsed AS STRING)
                ),
                responseFormat => '{{"type":"json_schema","json_schema":'
                    || '{{"name":"invoice","schema":{schema_json}}}}}'
            ) AS STRING
        ) AS extraction_json
    FROM parsed
)
SELECT
    file_name,
    extraction_json,
    get_json_object(extraction_json, '$.invoice_no')  AS invoice_no,
    get_json_object(extraction_json, '$.po_number')   AS po_number,
    get_json_object(extraction_json, '$.customer')    AS customer,
    get_json_object(extraction_json, '$.currency')    AS currency,
    CAST(get_json_object(extraction_json, '$.subtotal') AS DOUBLE) AS subtotal,
    CAST(get_json_object(extraction_json, '$.tax')      AS DOUBLE) AS tax,
    CAST(get_json_object(extraction_json, '$.total')    AS DOUBLE) AS total
FROM extracted
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
    """Return SQL joining inspections to labels and computing correctness."""
    return f"""
CREATE OR REPLACE TABLE `{catalog}`.`silver`.`container_inspections_scored` AS
SELECT
    i.file_name,
    l.container_no,
    get_json_object(i.inspection_json, '$.damage')            AS pred_damage,
    get_json_object(i.inspection_json, '$.damage_type')       AS pred_damage_type,
    CAST(get_json_object(i.inspection_json, '$.confidence') AS DOUBLE) AS confidence,
    get_json_object(i.inspection_json, '$.recommended_action') AS recommended_action,
    l.gt_damage,
    l.gt_damage_type,
    CASE WHEN get_json_object(i.inspection_json, '$.damage') = l.gt_damage
         THEN 1 ELSE 0 END AS is_correct
FROM `{catalog}`.`silver`.`container_inspections` i
JOIN `{catalog}`.`silver`.`container_image_labels` l
  ON i.file_name = l.file_name
""".strip()
