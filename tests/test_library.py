"""Unit tests for the shared pil_workshop library (run off-platform).

    PYTHONPATH=src pytest tests

Covers: llm endpoint resolution + governance guard, ISO 6346 check digits,
deterministic data generation with KPIs in range, and forecasting metrics.
"""

from __future__ import annotations

import numpy as np
import pytest

from pil_workshop import config, datagen, llm
from pil_workshop.datagen.iso6346 import check_digit, container_number, is_valid
from pil_workshop.ml import (
    croston,
    make_enriched_features,
    mase,
    seasonal_naive,
    tsb,
    wape,
    wape_aggregated,
)


# --- llm.py: the single source of truth for endpoints ---------------------
def test_resolve_endpoints_picks_preferred_when_available():
    served = {"databricks-claude-sonnet-4", "databricks-gte-large-en"}
    r = llm.resolve_endpoints(available=served, region="southeastasia")
    assert r.text == "databricks-claude-sonnet-4"
    assert r.vision == "databricks-claude-sonnet-4"
    assert r.embedding == "databricks-gte-large-en"


def test_resolve_endpoints_degrades_and_notes_when_nothing_preferred():
    served = {"databricks-some-other-model"}
    r = llm.resolve_endpoints(available=served, region="southeastasia")
    assert r.text == "databricks-some-other-model"
    assert any("cross-geography" in n or "fell back" in n for n in r.notes)


def test_resolve_endpoints_no_endpoints_still_returns():
    r = llm.resolve_endpoints(available=set(), region="southeastasia")
    assert r.text  # never None
    assert r.notes


def test_governance_guard_rejects_external_host():
    with pytest.raises(RuntimeError):
        llm._assert_governed("https://api.openai.com/v1")
    # workspace serving URL is fine
    llm._assert_governed("https://adb-1.azuredatabricks.net/serving-endpoints")


# --- ISO 6346 -------------------------------------------------------------
def test_iso6346_known_check_digit_and_roundtrip():
    no = container_number("PIL", "U", 123456)
    assert is_valid(no)
    assert len(no) == 11
    # Corrupting the serial should break validity.
    assert not is_valid(no[:5] + ("0" if no[5] != "0" else "1") + no[6:])


def test_iso6346_check_digit_deterministic():
    assert check_digit("PILU", "123456") == check_digit("PILU", "123456")


# --- deterministic data generation + KPI construction ---------------------
def test_generate_all_is_deterministic_and_coherent():
    d1 = datagen.generate_all("demo")
    d2 = datagen.generate_all("demo")
    assert d1["voyage_legs"][0] == d2["voyage_legs"][0]
    assert len(d1["shipments"]) == len(d2["shipments"])
    # FK integrity: every leg's origin port exists.
    port_ids = {p["port_id"] for p in d1["ports"]}
    assert all(leg["origin_port_id"] in port_ids for leg in d1["voyage_legs"])


def test_generated_kpis_land_in_configured_ranges():
    d = datagen.generate_all("demo")
    legs = d["voyage_legs"]
    reliability = 100.0 * sum(1 for x in legs if x["on_time"]) / len(legs)
    util = 100.0 * sum(x["loaded_teu"] for x in legs) / sum(x["capacity_teu"] for x in legs)
    lo, hi = config.KPI_RANGES["schedule_reliability_pct"]
    assert lo <= reliability <= hi
    lo, hi = config.KPI_RANGES["vessel_utilization_pct"]
    assert lo <= util <= hi


def test_invoices_have_expected_anomaly_share():
    d = datagen.generate_all("demo")
    invs = d["invoices"]
    anomalies = [i for i in invs if i["gt_anomaly"]]
    # ~10% planted anomalies; allow a wide deterministic band.
    assert 0.04 <= len(anomalies) / len(invs) <= 0.16


def test_generate_increment_returns_only_incremental_tables_with_offset_ids():
    """The incremental batch holds only the event/txn tables, and its owned PKs
    are shifted past ``id_offset`` so appended rows never collide with the base."""
    from datetime import date

    off = 5_000_000
    inc = datagen.generate_increment("demo", id_offset=off, days=7,
                                     today=date(2026, 8, 8))
    assert set(inc.keys()) <= set(datagen.INCREMENTAL_TABLES)
    assert inc.get("bookings"), "expected a non-empty incremental slice"
    assert min(b["booking_id"] for b in inc["bookings"]) > off
    # Business-key strings are re-keyed off the offset PK so they stay unique.
    assert all(b["booking_no"] == f"BKG{b['booking_id']:08d}" for b in inc["bookings"])
    assert all(i["invoice_no"].startswith("INV-") for i in inc["invoices"])
    # Dimension FKs are preserved (unshifted) so they resolve against the base.
    base = datagen.generate_all("demo", today=date(2026, 8, 8))
    customer_ids = {c["customer_id"] for c in base["customers"]}
    assert all(b["customer_id"] in customer_ids for b in inc["bookings"])


def test_generate_increment_is_date_confined_to_recent_window():
    """A windowed increment dates its rows in the recent tail (not scattered
    across the full ~24-month history) so dashboard growth is visible."""
    from datetime import date, datetime, timedelta

    today = date(2026, 8, 8)
    _, end = config.history_window(today)
    days = 30
    inc = datagen.generate_increment("full", id_offset=1, days=days, today=today)
    bdates = [datetime.fromisoformat(b["booking_ts"]).date() for b in inc["bookings"]]
    # Every appended booking falls within (recent-window + generation slack) of
    # the window end — none land months/years back like the base load does.
    cutoff = end - timedelta(days=days + 40)
    assert min(bdates) >= cutoff, f"rows leaked before the recent window: {min(bdates)}"


def test_generate_increment_scales_with_days():
    """Slice row counts scale ~linearly with ``days`` (the tunable lever)."""
    from datetime import date

    today = date(2026, 8, 8)
    small = datagen.generate_increment("full", id_offset=1, days=7, today=today)
    big = datagen.generate_increment("full", id_offset=1, days=30, today=today)
    r = len(big["bookings"]) / max(len(small["bookings"]), 1)
    assert 3.0 <= r <= 5.5, f"expected ~4.3x more rows at 30d vs 7d, got {r:.1f}x"


def test_full_window_generation_unchanged_by_window_feature():
    """Regression: the base load (no window_start) still spans the FULL history,
    so the recent-window feature can't have altered default generation."""
    from datetime import date, datetime, timedelta

    today = date(2026, 8, 8)
    start, end = config.history_window(today)
    d = datagen.generate_all("demo", today=today)
    vdates = sorted(datetime.fromisoformat(v["departure_date"]).date()
                    for v in d["voyages"])
    # Earliest voyage is far back (full spread), not clustered near the end.
    assert vdates[0] < end - timedelta(days=400)


def test_bundled_real_container_photos_copy_with_matching_label_schema():
    """The bundled REAL container photos (assets/container_samples) must copy into
    the images Volume and carry the SAME label schema as the synthetic set, so
    notebook 07 can union them into silver.container_image_labels."""
    import os
    import tempfile
    from pathlib import Path

    from pil_workshop.datagen import unstructured

    samples = Path(__file__).resolve().parents[1] / "assets" / "container_samples"
    if not (samples / "labels.json").is_file():
        pytest.skip("bundled real photos not present")

    out = tempfile.mkdtemp()
    recs = unstructured.copy_real_container_images(str(samples), out)
    assert recs, "no real photos copied"
    synth_keys = {"file_name", "container_no", "gt_damage", "gt_damage_type"}
    for r in recs:
        assert set(r.keys()) == synth_keys
        assert r["gt_damage"] in ("none", "minor", "major")
        # the labelled file must actually have been copied to the volume dir
        assert os.path.isfile(os.path.join(out, r["file_name"]))
    # missing dir degrades gracefully (setup never halts)
    assert unstructured.copy_real_container_images("/no/such/dir", tempfile.mkdtemp()) == []


def test_invoice_pdf_numbers_are_stable_across_runs():
    """Regression (bug #2b): invoice_no / issue_date must NOT drift with the wall
    clock. Notebook 07 regenerates the PDFs on every 12-hourly run, but the
    review queue is seeded once — if invoice_no changed run-to-run (it used to
    embed date.today().year) the queue would point at stale documents.

    We patch date.today() to two very different dates and assert the generated
    ground truth is byte-identical (numbers, issue dates, totals all fixed).
    """
    import datetime as _dt
    import tempfile

    from pil_workshop.datagen import unstructured

    reportlab = pytest.importorskip("reportlab")  # noqa: F841 — PDF rendering dep

    class _FrozenDate(_dt.date):
        _frozen = _dt.date(2030, 1, 1)

        @classmethod
        def today(cls):
            return cls._frozen

    orig = unstructured.date
    try:
        # Run once as if "today" were 2030, once as 2020 — must be identical.
        _FrozenDate._frozen = _dt.date(2030, 1, 1)
        unstructured.date = _FrozenDate
        gt_2030 = unstructured.generate_invoice_pdfs(tempfile.mkdtemp(), n=25, seed=42)
        _FrozenDate._frozen = _dt.date(2020, 1, 1)
        gt_2020 = unstructured.generate_invoice_pdfs(tempfile.mkdtemp(), n=25, seed=42)
    finally:
        unstructured.date = orig

    def key(g):
        return (g["invoice_no"], g["issue_date"], g["total"], g["gt_anomaly"])

    assert [key(g) for g in gt_2030] == [key(g) for g in gt_2020]
    # And the year embedded in invoice_no reflects the FIXED anchor, not 2030/2020.
    years = {g["invoice_no"].split("-")[1] for g in gt_2030}
    assert years and years.isdisjoint({"2030", "2020"})


# --- forecasting metrics --------------------------------------------------
def test_wape_and_mase_basic():
    actual = np.array([10.0, 12.0, 11.0])
    forecast = np.array([9.0, 13.0, 10.0])
    assert 0 < wape(actual, forecast) < 0.2
    train = np.array([8.0, 9.0, 10.0, 11.0, 12.0, 10.0, 9.0, 11.0])
    assert mase(actual, forecast, train, season=7) >= 0


def test_croston_tsb_positive_for_intermittent():
    demand = np.array([0, 0, 5, 0, 0, 0, 8, 0, 3, 0])
    assert croston(demand) > 0
    assert tsb(demand) > 0
    assert seasonal_naive(demand, season=7, horizon=3).shape == (3,)


def test_wape_handles_all_zero_actual():
    assert wape(np.zeros(3), np.zeros(3)) == 0.0


def test_enriched_features_no_leakage_and_warmup():
    v = np.arange(80, dtype=float)
    f = make_enriched_features(v)
    # lag_k[i] must equal the value k steps earlier (no future leakage).
    assert f["lag_1"][10] == v[9]
    assert f["lag_7"][20] == v[13]
    # warmup rows (before the longest lag / rolling window) are NaN.
    assert np.isnan(f["lag_35"][5])
    assert np.isnan(f["rollmean_56"][10])
    # EWMA is shifted by one (row 0 has no past → NaN).
    assert np.isnan(f["ewma_7"][0])
    assert {"lag_1", "lag_35", "rollmean_56", "ewma_7", "ewma_28"} <= set(f)


def test_wape_aggregated_by_group_and_period():
    # Two groups; each group's TOTAL matches exactly → aggregated WAPE 0,
    # even though the per-day values differ (daily WAPE would be > 0).
    actual = np.array([1.0, 1.0, 1.0, 5.0, 5.0, 5.0])
    forecast = np.array([0.0, 0.0, 3.0, 4.0, 5.0, 6.0])
    groups = np.array([0, 0, 0, 1, 1, 1])
    assert abs(wape_aggregated(actual, forecast, groups, period=0)) < 1e-9
    assert wape(actual, forecast) > 0
    # period bucketing: identical series → 0 regardless of period length.
    a = np.arange(14, dtype=float)
    g = np.zeros(14)
    assert wape_aggregated(a, a, g, period=7) == 0.0


# --- Genie serialized_space builder (dbx_api) ---------------------------------
def test_build_serialized_space_v2_shape_and_sorting():
    import json

    from pil_workshop.dbx_api import build_serialized_space

    s = json.loads(
        build_serialized_space(
            ["c.gold.b_table", "c.gold.a_table"],  # intentionally unsorted
            text_instructions="Line one.\nLine two.",
            example_sqls=[
                {"question": "q1", "sql": "SELECT 1", "usage_guidance": "g1"},
                {"question": "q2", "sql": "SELECT 2"},
            ],
            benchmarks=[{"question": "bq", "sql": "SELECT 3"}],
            sample_questions=["Question B?", "Question A?"],
        )
    )
    assert s["version"] == 2
    # suggested questions live under config.sample_questions, sorted by id
    sq = s["config"]["sample_questions"]
    assert len(sq) == 2
    assert [q["id"] for q in sq] == sorted(q["id"] for q in sq)
    assert {q["question"][0] for q in sq} == {"Question A?", "Question B?"}
    # tables sorted by identifier
    idents = [t["identifier"] for t in s["data_sources"]["tables"]]
    assert idents == sorted(idents)
    ti = s["instructions"]["text_instructions"]
    assert len(ti) == 1 and ti[0]["content"] == ["Line one.\n", "Line two.\n"]
    ex = s["instructions"]["example_question_sqls"]
    assert len(ex) == 2
    # id-bearing lists must be sorted by id (proto requirement)
    assert [e["id"] for e in ex] == sorted(e["id"] for e in ex)
    assert all(len(e["id"]) == 32 for e in ex)  # uuid hex
    bq = s["benchmarks"]["questions"]
    assert len(bq) == 1 and bq[0]["answer"][0]["format"] == "SQL"


def test_build_serialized_space_minimal_tables_only():
    import json

    from pil_workshop.dbx_api import build_serialized_space

    s = json.loads(build_serialized_space(["c.g.t"]))
    assert s["version"] == 2
    assert s["data_sources"]["tables"] == [{"identifier": "c.g.t"}]
    assert "instructions" not in s and "benchmarks" not in s


def test_invoice_extraction_function_ddl_bakes_endpoint_and_name():
    from pil_workshop import agent_bricks as ab

    ddl = ab.build_invoice_extraction_function_ddl("pil_workshop", "databricks-claude-sonnet-4-5")
    # references the governed function name (catalog-qualified, backticked catalog)
    assert "`pil_workshop`.default.extract_invoice_fields" in ddl
    assert "CREATE OR REPLACE FUNCTION" in ddl
    assert "RETURNS STRING" in ddl
    # endpoint is baked in (UC SQL fn can't resolve it at call time)
    assert "ai_query(\n    'databricks-claude-sonnet-4-5'" in ddl
    # asks for the shared nested schema keys
    assert "line_items" in ddl and "payment_terms" in ddl


def test_single_invoice_sql_calls_uc_function_not_inline_ai_query():
    from pil_workshop import agent_bricks as ab

    sql = ab.build_single_invoice_extraction_sql("pil_workshop", "/Volumes/pil_workshop/bronze/raw_invoices/x.pdf")
    # parse + flat extract stay inline
    assert "ai_parse_document" in sql
    assert "ai_extract(text, array(" in sql
    # nested extraction is delegated to the governed UC function (no inline ai_query)
    assert "`pil_workshop`.default.extract_invoice_fields(text)" in sql
    assert "ai_query(" not in sql
    # returns the same two columns the app consumer expects
    assert "AS flat" in sql and "AS nested_json" in sql


def test_invoice_function_name_is_catalog_qualified():
    from pil_workshop import agent_bricks as ab

    assert ab.invoice_function_name("mycat") == "`mycat`.default.extract_invoice_fields"
