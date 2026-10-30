"""Tests for the COUNTY_SERIES registry (bps/saipe/laus channel migration).

Pins the registry contract + the exact column names/values the three
bespoke channels produced before the 2026-07-16 migration.
"""
from __future__ import annotations

import math

import pytest
from census_forecaster.acs.ml_features import (
    _AUX_COLUMNS,
    COUNTY_SERIES,
    build_panel_index,
    county_columns,
    county_series_columns,
    load_county_data,
    make_training_rows,
)
from census_forecaster.models import AcsObservation


def _obs(g, i, y, e):
    return AcsObservation(estimate=e, moe=1.0, year=y, vintage="1y",
                          geoid=g, indicator=i)


def _multi_series():
    out = {}
    for ind, base in (("B19013_001E", 80_000.0), ("B25077_001E", 5e5)):
        for g in ("15003", "15001"):
            out[(g, ind)] = [_obs(g, ind, y, base * 1.03 ** (y - 2012))
                             for y in range(2012, 2025)]
    return out


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------

def test_registry_has_three_series_twelve_columns():
    assert len(COUNTY_SERIES) == 3
    assert len(county_columns()) == 12


def test_column_names_are_exactly_the_pre_migration_names():
    """The migration must not rename a single column."""
    assert county_columns() == (
        "bps_log_lag0", "bps_log_lag1", "bps_log_lag2", "bps_3yr_mean",
        "saipe_lag0", "saipe_lag1", "saipe_lag2", "saipe_3yr_mean",
        "laus_lag0", "laus_lag1", "laus_lag2", "laus_3yr_mean",
    )


def test_county_columns_lead_the_aux_block():
    # County block comes first in _AUX_COLUMNS (order = registry order).
    assert _AUX_COLUMNS[:12] == county_columns()


def test_col_policy_shapes():
    for spec in COUNTY_SERIES:
        cols = county_series_columns(spec)
        assert len(cols) == 4
        assert cols[-1] == f"{spec.name}_3yr_mean"
        if spec.col_policy == "log_lags3_mean":
            assert cols[0] == f"{spec.name}_log_lag0"
        else:
            assert cols[0] == f"{spec.name}_lag0"


def test_unknown_policy_raises():
    from census_forecaster.acs.ml_features import CountySeriesSpec
    bad = CountySeriesSpec("x", "_X", "anchors", "x.json", "nonsense")
    with pytest.raises(ValueError, match="unknown col_policy"):
        county_series_columns(bad)


# ---------------------------------------------------------------------------
# Transform correctness per policy
# ---------------------------------------------------------------------------

def test_log_policy_log_scales_and_means_valid_lags():
    bps = {"15003": {2018: 100.0, 2019: 200.0, 2020: 400.0},
           "15001": {2018: 100.0, 2019: 200.0, 2020: 400.0}}
    panel = build_panel_index(_multi_series(), county_data={"bps": bps})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000},
                           "B19013_001E", cutoff_year=2024)
    cols = m.spec.column_names
    i0, i1, i2, im = (cols.index(c) for c in
                      ("bps_log_lag0", "bps_log_lag1", "bps_log_lag2",
                       "bps_3yr_mean"))
    row = next(r for r, meta in zip(m.X, m.meta) if meta[1] == 2020)
    assert row[i0] == pytest.approx(math.log(400.0))
    assert row[i1] == pytest.approx(math.log(200.0))
    assert row[i2] == pytest.approx(math.log(100.0))
    assert row[im] == pytest.approx(
        (math.log(400.0) + math.log(200.0) + math.log(100.0)) / 3)


def test_level_policy_keeps_raw_rate():
    laus = {"15003": {2019: 3.0, 2020: 9.0}, "15001": {2019: 3.0, 2020: 9.0}}
    panel = build_panel_index(_multi_series(), county_data={"laus": laus})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000},
                           "B19013_001E", cutoff_year=2024)
    cols = m.spec.column_names
    i0, im = cols.index("laus_lag0"), cols.index("laus_3yr_mean")
    row = next(r for r, meta in zip(m.X, m.meta) if meta[1] == 2020)
    assert row[i0] == pytest.approx(9.0)          # raw, not logged
    assert row[im] == pytest.approx((9.0 + 3.0) / 2)   # mean of VALID lags


def test_mean_of_valid_lags_ignores_missing():
    saipe = {"15003": {2020: 8.0}, "15001": {2020: 8.0}}   # only one lag
    panel = build_panel_index(_multi_series(), county_data={"saipe": saipe})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000},
                           "B19013_001E", cutoff_year=2024)
    cols = m.spec.column_names
    i1, im = cols.index("saipe_lag1"), cols.index("saipe_3yr_mean")
    row = next(r for r, meta in zip(m.X, m.meta) if meta[1] == 2020)
    assert math.isnan(row[i1])
    assert row[im] == pytest.approx(8.0)   # mean of the single valid lag


# ---------------------------------------------------------------------------
# Injection semantics
# ---------------------------------------------------------------------------

def test_non_positive_values_not_stored():
    panel = build_panel_index(
        _multi_series(),
        county_data={"bps": {"15003": {2019: 0, 2020: -5, 2021: 10}}})
    spec = next(s for s in COUNTY_SERIES if s.name == "bps")
    assert panel.get("15003", spec.sentinel, 2019) is None
    assert panel.get("15003", spec.sentinel, 2020) is None
    assert panel.get("15003", spec.sentinel, 2021) == 10.0


def test_unknown_series_name_ignored_not_guessed():
    panel = build_panel_index(
        _multi_series(), county_data={"not_a_registry_series": {"15003": {2020: 1.0}}})
    # No crash, nothing injected under any county sentinel.
    for spec in COUNTY_SERIES:
        assert panel.get("15003", spec.sentinel, 2020) is None


def test_sentinels_stay_out_of_indicators():
    panel = build_panel_index(
        _multi_series(), county_data={"laus": {"15003": {2020: 3.0}}})
    for spec in COUNTY_SERIES:
        assert spec.sentinel not in panel.indicators


def test_all_columns_nan_when_county_data_absent():
    panel = build_panel_index(_multi_series())
    m = make_training_rows(panel, {"15003": 1_000_000},
                           "B19013_001E", cutoff_year=2024)
    cols = m.spec.column_names
    for name in county_columns():
        i = cols.index(name)
        assert all(math.isnan(r[i]) for r in m.X)


# ---------------------------------------------------------------------------
# Bundled loader
# ---------------------------------------------------------------------------

def test_load_county_data_returns_registry_names():
    data = load_county_data()
    names = {s.name for s in COUNTY_SERIES}
    assert set(data) <= names
    for name, by_geoid in data.items():
        assert by_geoid
        for geoid, by_year in by_geoid.items():
            assert all(isinstance(y, int) for y in by_year)
            assert all(math.isfinite(v) for v in by_year.values())
