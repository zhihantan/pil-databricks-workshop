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
from pil_workshop.ml import croston, mase, seasonal_naive, tsb, wape


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
