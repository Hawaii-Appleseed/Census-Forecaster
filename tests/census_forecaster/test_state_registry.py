"""Tests for the STATE_SERIES registry (DOL UI-claims channel).

The third geographic tier: county-varying (COUNTY_SERIES) and
geoid-constant (market / national-macro) already existed; a state series
gives every county in a state the same value, resolved from its own
state FIPS. Pins the registry contract, the four column transforms, the
per-state fan-out, and the column-order invariant the aux block depends
on.
"""
from __future__ import annotations

import math

import pytest

from census_forecaster.acs.ml_features import (
    _AUX_COLUMNS,
    STATE_SERIES,
    StateSeriesSpec,
    _state_geoid,
    build_panel_index,
    load_state_data,
    make_feature_spec,
    make_training_rows,
    state_columns,
    state_series_columns,
)
from census_forecaster.models import AcsObservation


def _obs(g, i, y, e):
    return AcsObservation(estimate=e, moe=1.0, year=y, vintage="1y",
                          geoid=g, indicator=i)


def _multi_series(geoids=("15003", "15001", "06037")):
    """Two indicators over two Hawaii counties and one California county."""
    out = {}
    for ind, base in (("B19013_001E", 80_000.0), ("B25077_001E", 5e5)):
        for g in geoids:
            out[(g, ind)] = [_obs(g, ind, y, base * 1.03 ** (y - 2012))
                             for y in range(2012, 2025)]
    return out


def _row_for(matrix, geoid, anchor, horizon=1):
    for meta, row in zip(matrix.meta, matrix.X):
        if meta[0] == geoid and meta[1] == anchor and meta[3] == horizon:
            return row
    raise AssertionError(f"no row for {geoid} @ {anchor} h={horizon}")


def _cols(matrix, names):
    idx = [matrix.spec.column_names.index(n) for n in names]
    return idx


# ---------------------------------------------------------------------------
# Registry contract
# ---------------------------------------------------------------------------

def test_registry_has_one_series_four_columns():
    assert len(STATE_SERIES) == 1
    assert state_columns() == (
        "ui_claims_log_lag0", "ui_claims_chg1",
        "ui_claims_chg2", "ui_claims_rel3",
    )


def test_state_columns_trail_the_aux_block():
    """Appended LAST so every pre-existing column keeps its slot index."""
    assert _AUX_COLUMNS[-4:] == state_columns()


def test_unknown_policy_raises():
    bad = StateSeriesSpec("x", "_X", "leading_indicators", "x.json", "nonsense")
    with pytest.raises(ValueError, match="unknown col_policy"):
        state_series_columns(bad)


def test_state_geoid_is_zero_padded():
    assert _state_geoid(15) == "__state_15__"
    assert _state_geoid(6) == "__state_06__"


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

_CLAIMS = {"15": {2017: 1000.0, 2018: 2000.0, 2019: 3000.0, 2020: 6000.0}}


def test_column_transforms_are_the_documented_ratios():
    panel = build_panel_index(_multi_series(),
                             state_data={"ui_claims": _CLAIMS})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000,
                                   "06037": 10_000_000},
                           "B19013_001E", cutoff_year=2024)
    row = _row_for(m, "15003", 2020)
    i0, i1, i2, i3 = _cols(m, state_columns())
    assert row[i0] == pytest.approx(math.log(6000.0))
    assert row[i1] == pytest.approx(math.log(6000.0 / 3000.0))
    assert row[i2] == pytest.approx(math.log(6000.0 / 2000.0))
    baseline = (3000.0 + 2000.0 + 1000.0) / 3.0
    assert row[i3] == pytest.approx(math.log(6000.0 / baseline))


def test_three_of_four_columns_are_scale_free():
    """A state 100x larger must produce identical ratio columns.

    This is the property that makes the channel poolable across 51 states
    whose claim levels span two orders of magnitude.
    """
    claims = {"15": {y: v for y, v in _CLAIMS["15"].items()},
              "06": {y: v * 100 for y, v in _CLAIMS["15"].items()}}
    panel = build_panel_index(_multi_series(),
                             state_data={"ui_claims": claims})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000,
                                   "06037": 10_000_000},
                           "B19013_001E", cutoff_year=2024)
    hi = _row_for(m, "15003", 2020)
    ca = _row_for(m, "06037", 2020)
    i0, i1, i2, i3 = _cols(m, state_columns())
    for i in (i1, i2, i3):
        assert hi[i] == pytest.approx(ca[i])
    assert ca[i0] == pytest.approx(hi[i0] + math.log(100))


def test_every_county_in_a_state_shares_its_states_values():
    panel = build_panel_index(_multi_series(),
                             state_data={"ui_claims": _CLAIMS})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000,
                                   "06037": 10_000_000},
                           "B19013_001E", cutoff_year=2024)
    idx = _cols(m, state_columns())
    a = _row_for(m, "15003", 2020)
    b = _row_for(m, "15001", 2020)
    assert [a[i] for i in idx] == [b[i] for i in idx]


def test_county_in_an_unsupplied_state_nan_fills():
    """California has no claims here — its columns must go NaN, not borrow."""
    panel = build_panel_index(_multi_series(),
                             state_data={"ui_claims": _CLAIMS})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000,
                                   "06037": 10_000_000},
                           "B19013_001E", cutoff_year=2024)
    ca = _row_for(m, "06037", 2020)
    for i in _cols(m, state_columns()):
        assert math.isnan(ca[i])


def test_rel3_uses_whatever_prior_years_exist():
    """With only one prior year the baseline is that year alone."""
    claims = {"15": {2019: 1000.0, 2020: 4000.0}}
    panel = build_panel_index(_multi_series(),
                             state_data={"ui_claims": claims})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000,
                                   "06037": 10_000_000},
                           "B19013_001E", cutoff_year=2024)
    row = _row_for(m, "15003", 2020)
    i0, i1, i2, i3 = _cols(m, state_columns())
    assert row[i1] == pytest.approx(math.log(4.0))
    assert math.isnan(row[i2])                       # no 2018
    assert row[i3] == pytest.approx(math.log(4.0))   # baseline = 2019 alone


def test_missing_anchor_year_nan_fills_every_column():
    claims = {"15": {2017: 1000.0, 2018: 2000.0}}   # nothing at 2020
    panel = build_panel_index(_multi_series(),
                             state_data={"ui_claims": claims})
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000,
                                   "06037": 10_000_000},
                           "B19013_001E", cutoff_year=2024)
    row = _row_for(m, "15003", 2020)
    for i in _cols(m, state_columns()):
        assert math.isnan(row[i])


# ---------------------------------------------------------------------------
# Injection guards
# ---------------------------------------------------------------------------

def test_non_positive_values_are_treated_as_missing():
    claims = {"15": {2019: 0.0, 2020: -5.0, 2021: 1000.0}}
    panel = build_panel_index(_multi_series(),
                             state_data={"ui_claims": claims})
    assert panel.get("__state_15__", "_ST_UI_CLAIMS", 2019) is None
    assert panel.get("__state_15__", "_ST_UI_CLAIMS", 2020) is None
    assert panel.get("__state_15__", "_ST_UI_CLAIMS", 2021) == 1000.0


def test_unknown_series_name_is_ignored_not_guessed():
    panel = build_panel_index(
        _multi_series(), state_data={"not_a_registry_series": _CLAIMS})
    assert not any(k[1] == "_ST_UI_CLAIMS" for k in panel.estimate_by_key)


def test_int_and_unpadded_state_keys_both_resolve():
    panel = build_panel_index(
        _multi_series(), state_data={"ui_claims": {6: {2020: 500.0},
                                                  "6": {2021: 600.0}}})
    assert panel.get("__state_06__", "_ST_UI_CLAIMS", 2020) == 500.0
    assert panel.get("__state_06__", "_ST_UI_CLAIMS", 2021) == 600.0


def test_state_sentinel_is_not_a_cross_indicator_feature():
    """The auxiliary must never leak into another target's x_* columns."""
    panel = build_panel_index(_multi_series(),
                             state_data={"ui_claims": _CLAIMS})
    spec = make_feature_spec("B19013_001E", panel)
    assert not any("_ST_" in c for c in spec.cross_indicator_columns)


def test_all_columns_nan_when_state_data_absent():
    panel = build_panel_index(_multi_series())
    m = make_training_rows(panel, {"15003": 1_000_000, "15001": 200_000,
                                   "06037": 10_000_000},
                           "B19013_001E", cutoff_year=2024)
    row = _row_for(m, "15003", 2020)
    for i in _cols(m, state_columns()):
        assert math.isnan(row[i])


# ---------------------------------------------------------------------------
# Bundled file
# ---------------------------------------------------------------------------

def test_bundled_file_covers_every_panel_state():
    """51 FIPS-keyed states, Hawaii present, values sane."""
    data = load_state_data()
    assert set(data) == {"ui_claims"}
    claims = data["ui_claims"]
    assert len(claims) == 51
    assert all(len(k) == 2 and k.isdigit() for k in claims)
    hi = claims["15"]
    # Hawaii's COVID year must tower over its normal range (~1,000-1,500
    # weekly filings) — the same sanity signature the monthly screen
    # series is checked against.
    assert hi[2020] > 5 * hi[2019]
    assert 500 < hi[2019] < 2_500


def test_bundled_file_spans_the_calibration_anchors():
    claims = load_state_data()["ui_claims"]
    for fips, years in claims.items():
        assert min(years) <= 2007, fips     # lag-3 room for anchor 2010
        assert max(years) >= 2024, fips
