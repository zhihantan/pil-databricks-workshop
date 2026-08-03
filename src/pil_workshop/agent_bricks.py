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

# The single instruction string used by the nested ai_query extraction, shared
# by the batch pipeline, the app query, and the governed UC function so all three
# ask the model for the exact same JSON shape. Rich ~22-field schema covering
# header, parties, freight/shipping specifics, and money. Missing fields must be
# null (never invented) so downstream logic can trust absence.
_NESTED_INSTRUCTION = (
    "You are extracting a commercial/freight invoice into JSON. Return ONLY a "
    "JSON object (no prose, no code fences) with EXACTLY these keys; use null "
    "for anything not present — never guess:\n"
    "  invoice_no, invoice_date, due_date, purchase_order (PO number),\n"
    "  vendor_name, vendor_tax_id, vendor_address,\n"
    "  customer_name, customer_address,\n"
    "  currency (ISO 4217 code, e.g. USD/EUR/GBP/JPY/CNY/SGD),\n"
    "  incoterms, bill_of_lading, vessel_name, container_numbers (array of "
    "strings), port_of_loading, port_of_discharge,\n"
    "  payment_terms, bank_details,\n"
    "  line_items (array of objects: description, quantity, unit_price, amount),\n"
    "  subtotal, discount, shipping, tax, tax_rate, total, amount_paid, "
    "balance_due,\n"
    "  notes.\n"
    "Normalize all monetary values to plain numbers (no thousands separators, "
    "use '.' as the decimal point even if the source uses commas). Convert any "
    "currency symbol to its ISO code."
)

# Fully-qualified name of the governed UC function that wraps the nested
# ai_query extraction. Created by setup (notebook 08) and called by the app so
# the extraction "intelligence" is a versioned, UC-permissioned, reusable asset.
INVOICE_EXTRACT_FUNCTION = "default.extract_invoice_fields"


def invoice_function_name(catalog: str) -> str:
    """Fully-qualified name of the governed invoice-extraction UC function."""
    return f"`{catalog}`.{INVOICE_EXTRACT_FUNCTION}"


# ---------------------------------------------------------------------------
# Delta sink for app-uploaded invoice extractions.
#
# The app writes each extraction here (parameterized INSERT via the SQL
# connector) so a JSON extraction becomes a queryable Delta row. Typed columns
# for the fields you'd filter/aggregate on; arrays/nested kept as-is
# (container_numbers as ARRAY, line_items + raw_json as JSON strings) so nothing
# is lost. INVOICE_UPLOAD_COLUMNS is the single source of truth shared by the
# DDL and the INSERT so they never drift.
# ---------------------------------------------------------------------------
INVOICE_UPLOADS_TABLE = "apps.invoice_extractions_app"

# (column_name, sql_type) in insert order. extracted_at is defaulted, not passed.
INVOICE_UPLOAD_COLUMNS: tuple[tuple[str, str], ...] = (
    ("source_file", "STRING"),
    ("volume_path", "STRING"),
    ("invoice_no", "STRING"),
    ("invoice_date", "STRING"),
    ("due_date", "STRING"),
    ("purchase_order", "STRING"),
    ("vendor_name", "STRING"),
    ("vendor_tax_id", "STRING"),
    ("vendor_address", "STRING"),
    ("customer_name", "STRING"),
    ("customer_address", "STRING"),
    ("currency", "STRING"),
    ("incoterms", "STRING"),
    ("bill_of_lading", "STRING"),
    ("vessel_name", "STRING"),
    ("container_numbers", "ARRAY<STRING>"),
    ("port_of_loading", "STRING"),
    ("port_of_discharge", "STRING"),
    ("payment_terms", "STRING"),
    ("bank_details", "STRING"),
    ("notes", "STRING"),
    ("subtotal", "DOUBLE"),
    ("discount", "DOUBLE"),
    ("shipping", "DOUBLE"),
    ("tax", "DOUBLE"),
    ("tax_rate", "STRING"),
    ("total", "DOUBLE"),
    ("amount_paid", "DOUBLE"),
    ("balance_due", "DOUBLE"),
    ("line_items_json", "STRING"),
    ("exception_type", "STRING"),
    ("model_endpoint", "STRING"),
    ("est_total_tokens", "BIGINT"),
    ("raw_json", "STRING"),
)


def invoice_uploads_table(catalog: str) -> str:
    """Fully-qualified name of the app's invoice-extraction Delta sink."""
    return f"`{catalog}`.`apps`.`invoice_extractions_app`"


def build_invoice_uploads_ddl(catalog: str) -> str:
    """Return CREATE TABLE IF NOT EXISTS for the app's invoice extraction sink.

    ``extracted_at`` defaults to the write time (Delta column defaults) so the
    app never has to pass a timestamp. All other columns come from
    ``INVOICE_UPLOAD_COLUMNS`` so the DDL and the INSERT stay in lockstep.
    """
    cols = ",\n    ".join(f"`{name}` {sqltype}" for name, sqltype in INVOICE_UPLOAD_COLUMNS)
    return f"""
CREATE TABLE IF NOT EXISTS {invoice_uploads_table(catalog)} (
    `extracted_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
    {cols}
)
USING DELTA
COMMENT 'Structured invoice extractions written by the PIL app upload flow — '
        'one row per uploaded PDF (typed fields + line_items_json + raw_json).'
TBLPROPERTIES ('delta.feature.allowColumnDefaults' = 'supported')
""".strip()


def build_invoice_extraction_function_ddl(catalog: str, text_endpoint: str) -> str:
    """Return DDL for the governed invoice-extraction UC function.

    Wraps the nested ``ai_query`` call (the LLM "intelligence", and the part that
    hits the governed FMAPI endpoint) as a reusable, UC-permissioned function
    ``<catalog>.default.extract_invoice_fields(doc_text) -> STRING`` (JSON).

    The endpoint name is baked in at creation because a UC SQL function can't
    call the SDK to resolve it; setup recreates the function each run, so it
    self-heals if the resolved endpoint changes. ``ai_parse_document`` and
    ``ai_extract`` are intentionally NOT wrapped — the parser needs a constant
    ``READ_FILES`` path and ``ai_extract`` cannot compile inside a function body
    (its label array is rejected as non-constant); both stay inline at the call
    site.
    """
    endpoint = text_endpoint.replace("'", "''")
    instruction = _NESTED_INSTRUCTION.replace("'", "''")
    return f"""
CREATE OR REPLACE FUNCTION {invoice_function_name(catalog)}(doc_text STRING)
RETURNS STRING
COMMENT 'PIL freight/commercial invoice extraction via governed FMAPI ({endpoint}). '
        'Input: parsed invoice text. Output: rich JSON (~22 fields) — header '
        '(invoice_no, dates, PO), parties (vendor/customer name, address, tax id), '
        'freight (incoterms, bill_of_lading, vessel, containers[], ports), '
        'line_items[] (description, quantity, unit_price, amount), and money '
        '(subtotal, discount, shipping, tax, tax_rate, total, amount_paid, balance_due).'
RETURN ai_query(
    '{endpoint}',
    CONCAT('{instruction}\\n\\nTEXT:\\n', doc_text)
)
""".strip()


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
    -- Same instruction as the governed UC function so batch and app agree.
    SELECT file_name,
           ai_query(
               '{text_endpoint}',
               CONCAT('{_NESTED_INSTRUCTION}\\n\\nTEXT:\\n', text)
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
    -- normalize placeholder POs ('', '—', '-', 'n/a', 'none') to NULL so a
    -- genuinely missing PO is detectable downstream.
    CASE
        WHEN TRIM(LOWER(COALESCE(fl.f.po_number, ''))) IN ('', '—', '-', 'n/a', 'none', 'null')
        THEN NULL ELSE fl.f.po_number
    END                                                   AS po_number,
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


def build_single_invoice_extraction_sql(catalog: str, file_path: str) -> str:
    """Return SQL that extracts ONE invoice PDF at ``file_path`` in a single row.

    Used by the app's upload flow. Parses the PDF (``ai_parse_document``) and
    pulls flat header fields (``ai_extract``) inline — both must stay inline
    (parser needs a constant path; ``ai_extract`` can't compile in a UC
    function) — then delegates the nested/line-item extraction to the governed
    UC function ``<catalog>.default.extract_invoice_fields`` so the app and the
    batch pipeline share one versioned, permissioned extraction asset.

    Returns columns ``flat`` (struct), ``nested_json`` (raw model JSON — caller
    strips/parses) and ``doc_chars`` (parsed-text length, for token/cost
    estimation in the app).
    """
    # Escape single quotes in the path defensively (paths are server-controlled,
    # but keep this robust).
    safe_path = file_path.replace("'", "''")
    labels = ", ".join(f"'{lbl}'" for lbl in _EXTRACT_LABELS)
    return f"""
WITH parsed AS (
    SELECT array_join(
        transform(
            try_cast(ai_parse_document(content):document:elements AS ARRAY<STRING>),
            x -> get_json_object(x, '$.content')
        ), '\\n') AS text
    FROM READ_FILES('{safe_path}', format => 'binaryFile')
)
SELECT
    ai_extract(text, array({labels})) AS flat,
    {invoice_function_name(catalog)}(text) AS nested_json,
    LENGTH(text) AS doc_chars
FROM parsed
""".strip()


def build_invoice_reconciliation_sql(catalog: str) -> str:
    """Return SQL that flags invoice exceptions from the EXTRACTED data itself.

    Detects the three real-world problems directly from what was extracted — no
    ground-truth table required (a production deployment has none):
      * total_mismatch — total != subtotal + tax (internal inconsistency),
      * missing_po     — po_number is null after placeholder normalization,
      * duplicate_no   — the same invoice_no appears on more than one document.
    Writes one row per problem invoice → ``gold.invoice_exceptions``. Where a
    ground-truth table exists (this workshop), its label is joined for eval.
    """
    return f"""
CREATE OR REPLACE TABLE `{catalog}`.`gold`.`invoice_exceptions` AS
WITH e AS (
    SELECT * FROM `{catalog}`.`silver`.`invoice_extractions`
),
dup_counts AS (
    SELECT invoice_no, COUNT(*) AS n
    FROM e WHERE invoice_no IS NOT NULL GROUP BY invoice_no
),
flagged AS (
    SELECT
        e.file_name, e.invoice_no, e.customer, e.subtotal, e.tax, e.total,
        CASE
            WHEN e.total IS NOT NULL
             AND ABS(e.total - (COALESCE(e.subtotal,0) + COALESCE(e.tax,0))) > 1.0
                THEN 'total_mismatch'
            WHEN e.po_number IS NULL THEN 'missing_po'
            WHEN dc.n > 1 THEN 'duplicate_no'
            ELSE NULL
        END AS exception_type
    FROM e
    LEFT JOIN dup_counts dc ON e.invoice_no = dc.invoice_no
)
SELECT
    f.file_name, f.invoice_no, f.customer, f.subtotal, f.tax, f.total,
    f.exception_type,
    g.total       AS gt_total,        -- for the workshop eval only
    g.gt_anomaly  AS ground_truth_anomaly
FROM flagged f
LEFT JOIN `{catalog}`.`silver`.`invoice_pdf_ground_truth` g
  ON f.file_name = g.file_name
WHERE f.exception_type IS NOT NULL
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
