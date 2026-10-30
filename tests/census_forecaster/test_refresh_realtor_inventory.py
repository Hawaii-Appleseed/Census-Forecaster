"""Tests for the Realtor.com listing-metrics fetcher (network-free)."""

from __future__ import annotations

import json

import pytest
from census_forecaster.scripts import refresh_realtor_inventory as rdc
from lab.markets.screen import (
    HAWAII_PREDICTORS,
    HYPOTHESIS_PAIRS,
    MONTHLY_TARGETS,
)

HEADER = ("month_date_yyyymm,county_fips,county_name,median_listing_price,"
          "active_listing_count,median_days_on_market,new_listing_count,"
          "price_increased_share,price_reduced_share,pending_listing_count,"
          "median_listing_price_per_square_foot,pending_ratio,quality_flag")


def _row(month, fips, name, dom="70", active="3000", price="650000",
         new="900", pend="1000", pratio="0.33", cuts="0.12", hikes="0.01",
         ppsf="700", flag="0.0"):
    # county_name carries an embedded comma and is quoted in the source —
    # a split(",") parser silently shifts every later column.
    return (f'{month},{fips},"{name}",{price},{active},{dom},{new},{hikes},'
            f'{cuts},{pend},{ppsf},{pratio},{flag}')


def test_parses_hawaii_counties_and_shapes_rows():
    out = rdc.parse_history_rows([
        HEADER,
        _row("201607", "15003", "honolulu, hi", dom="55"),
        _row("202607", "15003", "honolulu, hi", dom="72"),
        _row("202607", "15009", "maui, hi", dom="90"),
    ])
    assert out["RDC_DOM_HONOLULU"] == [
        {"year": 2016, "period": "M07", "value": 55.0},
        {"year": 2026, "period": "M07", "value": 72.0},
    ]
    assert out["RDC_DOM_MAUI"] == [{"year": 2026, "period": "M07", "value": 90.0}]


def test_quoted_county_name_does_not_shift_columns():
    """Regression: the embedded comma in 'honolulu, hi' must not be
    treated as a field separator, or DOM reads back a listing price."""
    out = rdc.parse_history_rows([
        HEADER, _row("202607", "15003", "honolulu, hi", dom="72",
                     price="650000")])
    assert out["RDC_DOM_HONOLULU"][0]["value"] == 72.0
    assert out["RDC_LIST_PRICE_HONOLULU"][0]["value"] == 650000.0


def test_non_hawaii_counties_dropped():
    out = rdc.parse_history_rows([
        HEADER,
        _row("202607", "06037", "los angeles, ca"),
        _row("202607", "15003", "honolulu, hi"),
    ])
    assert set(out) == {p + "HONOLULU" for p in rdc.METRICS.values()}


def test_missing_and_na_values_skipped_not_zeroed():
    out = rdc.parse_history_rows([
        HEADER,
        _row("202606", "15003", "honolulu, hi", dom="NA"),
        _row("202607", "15003", "honolulu, hi", dom=""),
        _row("202608", "15003", "honolulu, hi", dom="61"),
    ])
    assert out["RDC_DOM_HONOLULU"] == [
        {"year": 2026, "period": "M08", "value": 61.0}]
    # other columns on the same rows still land
    assert len(out["RDC_ACTIVE_HONOLULU"]) == 3


@pytest.mark.parametrize("month", ["2026", "202613", "20260a", ""])
def test_bad_month_stamps_skipped(month):
    """Malformed stamps drop their row without taking good rows with
    them (short, out-of-range, non-numeric, empty)."""
    out = rdc.parse_history_rows([
        HEADER,
        _row(month, "15003", "honolulu, hi", dom="999"),
        _row("202607", "15003", "honolulu, hi", dom="72"),
    ])
    assert out["RDC_DOM_HONOLULU"] == [
        {"year": 2026, "period": "M07", "value": 72.0}]


def test_schema_drift_raises_rather_than_silently_emptying():
    """If Realtor.com renames a column the run must fail loudly — a
    silent empty result would merge nothing and look like success."""
    with pytest.raises(ValueError, match="missing expected columns"):
        rdc.parse_history_rows(["month_date_yyyymm,county_fips,county_name",
                                '202607,15003,"honolulu, hi"'])


def test_no_hawaii_rows_raises():
    with pytest.raises(ValueError, match="no Hawaii counties matched"):
        rdc.parse_history_rows([HEADER, _row("202607", "06037", "la, ca")])


def test_rows_sorted_chronologically():
    out = rdc.parse_history_rows([
        HEADER,
        _row("202607", "15003", "honolulu, hi", dom="3"),
        _row("201607", "15003", "honolulu, hi", dom="1"),
        _row("202001", "15003", "honolulu, hi", dom="2"),
    ])
    assert [r["value"] for r in out["RDC_DOM_HONOLULU"]] == [1.0, 2.0, 3.0]


def test_merge_is_additive(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"series": {"KEEP": [1]}, "sources": {},
                                "limitations": []}))
    merged = rdc.merge_into_macro_monthly(
        {"RDC_DOM_HONOLULU": [{"year": 2026, "period": "M07", "value": 72.0}]},
        path=path)
    assert merged["series"]["KEEP"] == [1]
    assert "REALTOR_RDC" in merged["sources"]
    # the limitations note is added exactly once across repeat runs
    again = rdc.merge_into_macro_monthly({"RDC_DOM_HONOLULU": []}, path=path)
    assert len([n for n in again["limitations"] if "RDC_*" in n]) == 1


def test_kalawao_absent_is_expected_not_a_bug():
    """15005 has ~80 residents and no listing market; the source omits
    it. Pinned so a future 'missing county' hunt does not chase it."""
    assert "15005" not in rdc.GEOS
    assert set(rdc.GEOS) == {"15001", "15003", "15007", "15009"}


# ---------------------------------------------------------------------------
# Screen wiring
# ---------------------------------------------------------------------------

def test_listing_predictors_wired_to_emitted_series():
    emitted = {p + g for p in rdc.METRICS.values() for g in rdc.GEOS.values()}
    for name in ("HI_DOM", "HI_PRICE_CUTS", "HI_PENDING_RATIO"):
        assert HAWAII_PREDICTORS[name] in emitted


def test_model_free_price_target_registered():
    """The contamination control: a recorded-sale price target that no
    estimator touches, so listing-derived predictors can be tested
    against something ZHVI's construction cannot explain."""
    assert MONTHLY_TARGETS["HONOLULU_SF_MEDIAN"] == (
        "DBEDT_SF_MEDIAN_HONOLULU", "log_diff")
    for name in ("HI_DOM", "HI_PRICE_CUTS", "HI_PENDING_RATIO"):
        assert (name, "HONOLULU_SF_MEDIAN") in HYPOTHESIS_PAIRS


def test_asking_price_deliberately_not_screened():
    """RDC_LIST_PRICE is a Zestimate input (circular vs ZHVI) and is
    near-tautological against a median SALE price. Bundled, not tested."""
    assert "HI_LIST_PRICE" not in HAWAII_PREDICTORS
    screened = {p for p, _ in HYPOTHESIS_PAIRS}
    assert not any(s.startswith("HI_LIST") for s in screened)


def test_price_hikes_not_screened_has_zeros():
    """price_increased_share hits exactly 0.0, which log_diff drops."""
    assert "HI_PRICE_HIKES" not in HAWAII_PREDICTORS
    assert "RDC_PRICE_HIKES_" in rdc.METRICS.values()   # still bundled
