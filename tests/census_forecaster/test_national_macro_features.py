"""Phase-3 tests: national-macro registry ML features (natl_<name>_* columns)."""
from __future__ import annotations

import math

import pytest

from census_forecaster.acs.ml_features import (
    _AUX_COLUMNS,
    _BASE_COLUMNS,
    _HORIZON_COLUMN,
    _NM_GEOID,
    NATIONAL_SERIES,
    build_panel_index,
    load_national_macro_data,
    make_training_rows,
    national_macro_columns,
    national_series_columns,
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


# Distinctive national values: a rate series (level_diff1) and an index
# series (logchange1).
NATL = {
    "dgs10":     {y: 2.0 + 0.1 * (y - 2012) for y in range(2011, 2025)},
    "cpi_rent":  {y: 300.0 * 1.03 ** (y - 2012) for y in range(2011, 2025)},
    "mortgage30": {y: 4.0 + 0.2 * (y - 2012) for y in range(2011, 2025)},
}


# ---------------------------------------------------------------------------
# Registry / column policy
# ---------------------------------------------------------------------------

def test_column_count_is_22_and_all_in_aux():
    # 13 original series (19 cols) + migrated national unemployment
    # ("unemp", level_diff2 → 3 cols) = 14 series, 22 cols.
    cols = national_macro_columns()
    assert len(cols) == 22
    assert all(c in _AUX_COLUMNS for c in cols)


def test_col_policy_shapes():
    for spec in NATIONAL_SERIES:
        cols = national_series_columns(spec)
        if spec.col_policy in ("logchange1", "diff1"):
            assert cols == (f"natl_{spec.name}_chg1",)
        elif spec.col_policy == "level_diff1":
            assert cols == (f"natl_{spec.name}_lvl", f"natl_{spec.name}_chg1")
        else:  # level_diff2
            assert cols == (f"natl_{spec.name}_lvl", f"natl_{spec.name}_chg1",
                            f"natl_{spec.name}_chg2")


# ---------------------------------------------------------------------------
# Column-order invariant (the recently-fixed bug must not regress)
# ---------------------------------------------------------------------------

def test_column_names_match_row_order_with_national_data():
    panel = build_panel_index(_multi_series(), national_data=NATL)
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000},
                           "B19013_001E", cutoff_year=2024)
    cols = m.spec.column_names
    assert m.spec.cross_indicator_columns          # need cross cols
    assert len(cols) == len(m.X[0])
    assert cols[:len(_BASE_COLUMNS)] == _BASE_COLUMNS
    assert cols[-1] == _HORIZON_COLUMN
    # named national columns hold their true values, per row
    i_lvl = cols.index("natl_dgs10_lvl")
    i_chg = cols.index("natl_dgs10_chg1")
    i_rent = cols.index("natl_cpi_rent_chg1")
    for row, (_g, anchor, _t, _h) in zip(m.X, m.meta):
        assert row[i_lvl] == pytest.approx(NATL["dgs10"][anchor])
        assert row[i_chg] == pytest.approx(
            NATL["dgs10"][anchor] - NATL["dgs10"][anchor - 1])
        assert row[i_rent] == pytest.approx(
            math.log(NATL["cpi_rent"][anchor] / NATL["cpi_rent"][anchor - 1]))


# ---------------------------------------------------------------------------
# Injection semantics
# ---------------------------------------------------------------------------

def test_sentinels_out_of_indicators_and_geoids():
    panel = build_panel_index(_multi_series(), national_data=NATL)
    assert all(not i.startswith("_NM_") for i in panel.indicators)
    assert _NM_GEOID not in panel.geoids
    assert panel.get(_NM_GEOID, "_NM_DGS10", 2020) == pytest.approx(2.8)


def test_diff1_is_pp_change_not_logchange():
    # mortgage30 uses diff1 → raw pp change, negatives allowed
    natl = {"mortgage30": {2019: 4.5, 2020: 3.1}}
    panel = build_panel_index(_multi_series(), national_data=natl)
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000},
                           "B19013_001E", cutoff_year=2024)
    i = m.spec.column_names.index("natl_mortgage30_chg1")
    for row, (_g, anchor, _t, _h) in zip(m.X, m.meta):
        if anchor == 2020:
            assert row[i] == pytest.approx(3.1 - 4.5)   # -1.4 pp


def test_nan_fill_when_absent():
    panel = build_panel_index(_multi_series())      # no national_data
    m = make_training_rows(panel, {"15003": 1_000_000},
                           "B19013_001E", cutoff_year=2024)
    cols = m.spec.column_names
    for name in national_macro_columns():
        i = cols.index(name)
        assert all(math.isnan(r[i]) for r in m.X)


def test_no_peek_change_needs_prior_year():
    natl = {"dgs10": {2020: 0.9}}                    # single year
    panel = build_panel_index(_multi_series(), national_data=natl)
    m = make_training_rows(panel, {"15003": 1_000_000},
                           "B19013_001E", cutoff_year=2024)
    cols = m.spec.column_names
    i_lvl, i_chg = cols.index("natl_dgs10_lvl"), cols.index("natl_dgs10_chg1")
    for row, (_g, anchor, _t, _h) in zip(m.X, m.meta):
        if anchor == 2020:
            assert row[i_lvl] == pytest.approx(0.9)
        assert math.isnan(row[i_chg])                # never computable


# ---------------------------------------------------------------------------
# Bundled loader
# ---------------------------------------------------------------------------

def test_bundled_national_macro_loads():
    data = load_national_macro_data()
    if data is None:
        pytest.skip("national_macro.json not committed")
    # Equality, not subset: the 2026-08-06 and 2026-09-23 refreshes each
    # dropped the four FRED series, and a subset check accepted the loss.
    names = {s.name for s in NATIONAL_SERIES}
    assert set(data) == names, f"missing: {sorted(names - set(data))}"
    for name, by_year in data.items():
        assert by_year
        assert all(isinstance(y, int) for y in by_year)
        assert all(math.isfinite(v) for v in by_year.values())
