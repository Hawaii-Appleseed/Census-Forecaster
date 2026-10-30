"""Tests for market-signal ML features (mkt_* columns in ml_features)."""
from __future__ import annotations

import math

import pytest
from census_forecaster.acs.ml_features import (
    _AUX_COLUMNS,
    _BASE_COLUMNS,
    _HORIZON_COLUMN,
    _MKT_GEOID,
    build_panel_index,
    load_market_signals_data,
    make_feature_spec,
    make_training_rows,
)
from census_forecaster.models import AcsObservation


def _obs(geoid, indicator, year, estimate):
    return AcsObservation(estimate=estimate, moe=1.0, year=year,
                          vintage="1y", geoid=geoid, indicator=indicator)


def _series(geoid="15003", indicator="B19013_001E",
            years=range(2012, 2025), base=80_000.0, growth=0.03):
    return {
        (geoid, indicator): [
            _obs(geoid, indicator, y, base * (1 + growth) ** (y - 2012))
            for y in years
        ]
    }


def _multi_series(geoids=("15003", "15001"),
                  indicators=(("B19013_001E", 80_000.0),
                              ("B25077_001E", 500_000.0))):
    """Multi-indicator panel → cross_indicator_columns is non-empty."""
    out = {}
    for g in geoids:
        for ind, base in indicators:
            out.update(_series(g, ind, base=base))
    return out


MARKET = {
    "mkt_energy_mom": {y: 0.10 + 0.01 * (y - 2012) for y in range(2012, 2025)},
    "mkt_shipping_mom": {y: -0.05 for y in range(2012, 2025)},
    "mkt_reit_mom": {y: 0.02 * (y - 2012) for y in range(2012, 2025)},
}

_MKT_COLS = ("mkt_energy_mom_lag0", "mkt_shipping_mom_lag0",
             "mkt_reit_mom_lag0", "mkt_reit_mom_lag1")


# ---------------------------------------------------------------------------
# Column layout
# ---------------------------------------------------------------------------

def test_mkt_columns_in_aux_block():
    # The mkt block lives in the aux columns (after cross, before horizon).
    assert all(c in _AUX_COLUMNS for c in _MKT_COLS)


def test_column_names_match_actual_row_order():
    """REGRESSION: column_names must describe the REAL _build_row order.

    A prior version concatenated aux columns ahead of the cross block in
    column_names, so with any cross-indicator columns present the name→
    position map was off by the cross-column count (silent: the model is
    name-blind, but permutation importance read mislabeled columns).
    Multi-indicator panel + distinctive aux values pin every named column
    to its true row slot."""
    laus = {"15003": {y: 3.5 for y in range(2012, 2025)},
            "15001": {y: 4.5 for y in range(2012, 2025)}}
    panel = build_panel_index(_multi_series(), county_data={"laus": laus},
                              market_data=MARKET)
    matrix = make_training_rows(
        panel, {"15003": 1_000_000, "15001": 200_000},
        "B19013_001E", cutoff_year=2024)
    assert matrix.spec.cross_indicator_columns, "need cross cols for this test"
    cols = matrix.spec.column_names
    assert len(cols) == len(matrix.X[0])
    # Base columns come first, cross next, aux after, horizon last.
    n_base = len(_BASE_COLUMNS)
    assert cols[:n_base] == _BASE_COLUMNS
    assert cols[-1] == _HORIZON_COLUMN
    # The named aux columns must hold their real values, per row.
    i_laus = cols.index("laus_lag0")
    i_energy = cols.index("mkt_energy_mom_lag0")
    for row, (geoid, anchor, _t, _h) in zip(matrix.X, matrix.meta):
        assert row[i_laus] == pytest.approx(3.5 if geoid == "15003" else 4.5)
        assert row[i_energy] == pytest.approx(MARKET["mkt_energy_mom"][anchor])


def test_spec_column_order_stable_with_market_data():
    series = _series()
    plain = make_feature_spec("B19013_001E", build_panel_index(series))
    with_mkt = make_feature_spec(
        "B19013_001E", build_panel_index(series, market_data=MARKET))
    assert plain.column_names == with_mkt.column_names


# ---------------------------------------------------------------------------
# Injection semantics
# ---------------------------------------------------------------------------

def test_sentinels_kept_out_of_indicators_and_geoids():
    panel = build_panel_index(_series(), market_data=MARKET)
    assert all(not i.startswith("_MKT") for i in panel.indicators)
    assert _MKT_GEOID not in panel.geoids
    assert panel.get(_MKT_GEOID, "_MKT_ENERGY_MOM", 2020) == pytest.approx(0.18)


def test_negative_momentum_values_survive_injection():
    panel = build_panel_index(_series(), market_data=MARKET)
    assert panel.get(_MKT_GEOID, "_MKT_SHIPPING_MOM", 2020) == pytest.approx(-0.05)


def test_rows_carry_market_values_geoid_constant():
    series = {**_series("15003"), **_series("15001")}
    panel = build_panel_index(series, market_data=MARKET)
    matrix = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000},
                                "B19013_001E", cutoff_year=2024)
    assert matrix.X, "no training rows built"
    cols = matrix.spec.column_names
    i_energy = cols.index("mkt_energy_mom_lag0")
    i_reit0 = cols.index("mkt_reit_mom_lag0")
    i_reit1 = cols.index("mkt_reit_mom_lag1")

    for row, (geoid, anchor, _t, _h) in zip(matrix.X, matrix.meta):
        assert row[i_energy] == pytest.approx(MARKET["mkt_energy_mom"][anchor])
        assert row[i_reit0] == pytest.approx(MARKET["mkt_reit_mom"][anchor])
        assert row[i_reit1] == pytest.approx(
            MARKET["mkt_reit_mom"][anchor - 1])

    # Geoid-constant: same anchor year → identical mkt features across counties.
    by_anchor = {}
    for row, (geoid, anchor, _t, h) in zip(matrix.X, matrix.meta):
        by_anchor.setdefault((anchor, h), []).append(row[i_energy])
    for vals in by_anchor.values():
        assert len(set(vals)) == 1


def test_rows_nan_fill_when_market_data_absent():
    panel = build_panel_index(_series())      # no market_data
    matrix = make_training_rows(panel, {"15003": 1_000_000},
                                "B19013_001E", cutoff_year=2024)
    assert matrix.X
    cols = matrix.spec.column_names
    for name in _MKT_COLS:
        i = cols.index(name)
        assert all(math.isnan(row[i]) for row in matrix.X)


def test_partial_market_coverage_nan_fills_missing_years():
    market = {"mkt_energy_mom": {2020: 0.5}}   # single year only
    panel = build_panel_index(_series(), market_data=market)
    matrix = make_training_rows(panel, {"15003": 1_000_000},
                                "B19013_001E", cutoff_year=2024)
    cols = matrix.spec.column_names
    i = cols.index("mkt_energy_mom_lag0")
    for row, (_g, anchor, _t, _h) in zip(matrix.X, matrix.meta):
        if anchor == 2020:
            assert row[i] == pytest.approx(0.5)
        else:
            assert math.isnan(row[i])


# ---------------------------------------------------------------------------
# Bundled loader (real committed file, if present)
# ---------------------------------------------------------------------------

def test_bundled_market_signals_load_and_align():
    data = load_market_signals_data()
    if data is None:
        pytest.skip("market_signals.json not yet committed")
    assert set(data) <= {"mkt_energy_mom", "mkt_shipping_mom", "mkt_reit_mom"}
    for name, by_year in data.items():
        assert by_year, name
        assert all(isinstance(y, int) for y in by_year)
        assert all(math.isfinite(v) for v in by_year.values())
        # momenta are log changes; anything beyond ±2 (≈ ±640%) is corrupt
        assert all(abs(v) < 2.0 for v in by_year.values()), name


# ---------------------------------------------------------------------------
# National-unemployment feature — MIGRATED to the NATIONAL_SERIES registry
# (2026-07-15): now the "unemp" entry with col_policy level_diff2, delivered
# via the national_data channel. Values are numerically identical to the
# former bespoke natl_unemp_data channel (lag0 renamed lvl). These tests pin
# the migrated behaviour; registry-wide tests live in
# test_national_macro_features.py.
# ---------------------------------------------------------------------------

_NATL_COLS = ("natl_unemp_lvl", "natl_unemp_chg1", "natl_unemp_chg2")
# national unemployment %: rises into a recession then recovers
NATL = {2012: 8.1, 2013: 7.4, 2014: 6.2, 2015: 5.3, 2016: 4.9,
        2017: 4.4, 2018: 3.9, 2019: 3.7, 2020: 8.1, 2021: 5.3,
        2022: 3.6, 2023: 3.6, 2024: 4.0}


def test_natl_unemp_columns_in_aux_block():
    assert all(c in _AUX_COLUMNS for c in _NATL_COLS)


def test_natl_unemp_level_and_changes_match_former_bespoke_values():
    """The migrated level_diff2 columns must reproduce the exact values the
    bespoke channel produced (lvl == old lag0; chg1/chg2 identical)."""
    panel = build_panel_index(_multi_series(), national_data={"unemp": NATL})
    matrix = make_training_rows(
        panel, {"15003": 1_000_000, "15001": 200_000},
        "B19013_001E", cutoff_year=2024)
    cols = matrix.spec.column_names
    i0 = cols.index("natl_unemp_lvl")
    i1 = cols.index("natl_unemp_chg1")
    i2 = cols.index("natl_unemp_chg2")
    for row, (_g, anchor, _t, _h) in zip(matrix.X, matrix.meta):
        assert row[i0] == pytest.approx(NATL[anchor])
        if anchor - 1 in NATL:
            assert row[i1] == pytest.approx(NATL[anchor] - NATL[anchor - 1])
        else:
            assert math.isnan(row[i1])
        if anchor - 2 in NATL:
            assert row[i2] == pytest.approx(NATL[anchor] - NATL[anchor - 2])
        else:
            assert math.isnan(row[i2])
    # geoid-constant: identical across counties at the same anchor
    by_anchor = {}
    for row, (_g, anchor, _t, h) in zip(matrix.X, matrix.meta):
        by_anchor.setdefault((anchor, h), set()).add(row[i0])
    assert all(len(v) == 1 for v in by_anchor.values())


def test_natl_unemp_no_peeking_change_needs_prior_year():
    # Only 2020 present → lvl known, but chg1/chg2 NaN (no prior years).
    panel = build_panel_index(_series(), national_data={"unemp": {2020: 8.1}})
    matrix = make_training_rows(panel, {"15003": 1_000_000},
                                "B19013_001E", cutoff_year=2024)
    cols = matrix.spec.column_names
    i0, i1 = cols.index("natl_unemp_lvl"), cols.index("natl_unemp_chg1")
    for row, (_g, anchor, _t, _h) in zip(matrix.X, matrix.meta):
        if anchor == 2020:
            assert row[i0] == pytest.approx(8.1)
        assert math.isnan(row[i1])   # never computable with one year


def test_bundled_unemp_matches_legacy_anchor_file():
    """national_macro.json 'unemp' must equal the legacy anchor file's
    values within rounding (3dp legacy vs 4dp registry) — proof the
    migration changed the plumbing, not the data."""
    import json
    from pathlib import Path

    from census_forecaster.acs.ml_features import load_national_macro_data
    data = load_national_macro_data()
    if data is None or "unemp" not in data:
        pytest.skip("national_macro.json missing 'unemp'")
    legacy_path = (Path(build_panel_index.__code__.co_filename).parent.parent
                   / "data" / "anchors" / "bls_national_unemployment.json")
    if not legacy_path.exists():
        pytest.skip("legacy anchor file absent")
    legacy = {int(y): float(v) for y, v in
              json.loads(legacy_path.read_text())["values_by_year"].items()}
    common = set(data["unemp"]) & set(legacy)
    assert common
    for y in common:
        assert data["unemp"][y] == pytest.approx(legacy[y], abs=1e-3)
