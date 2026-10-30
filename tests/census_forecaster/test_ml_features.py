"""Tests for the ml_features module.

Coverage:
* PanelIndex builds correctly from a synthetic series_by_key.
* FeatureSpec column ordering is deterministic (panel order, target excluded).
* Population one-hot classifier matches strata.classify_pop.
* Training-row generator respects walk-forward cutoff.
* Missing data is encoded as NaN (HGB handles natively).
* Inference row builder rejects when anchor or anchor-1 are missing.
"""
from __future__ import annotations

import math

import pytest
from census_forecaster.acs.ml_features import (
    _pop_one_hot,
    build_panel_index,
    make_feature_spec,
    make_inference_row,
    make_training_rows,
)
from census_forecaster.acs.strata import classify_pop
from census_forecaster.models import AcsObservation


def _obs(geoid, indicator, year, est, vintage="1y"):
    return AcsObservation(
        estimate=float(est), moe=float(est) * 0.05, year=year, vintage=vintage,
        geoid=geoid, indicator=indicator,
    )


def _toy_series():
    """Build a small panel: 2 indicators × 3 counties × 5 years."""
    series = {}
    for geoid, base in (("15003", 80000.0), ("15007", 60000.0), ("15001", 70000.0)):
        s_inc, s_age = [], []
        for i, year in enumerate(range(2018, 2023)):
            s_inc.append(_obs(geoid, "B19013_001E", year, base * (1.03 ** i)))
            s_age.append(_obs(geoid, "B01002_001E", year, 38.0 + i * 0.2))
        series[(geoid, "B19013_001E")] = s_inc
        series[(geoid, "B01002_001E")] = s_age
    return series


def test_panel_index_basic():
    series = _toy_series()
    panel = build_panel_index(series)
    assert "B19013_001E" in panel.indicators
    assert "B01002_001E" in panel.indicators
    assert "15003" in panel.geoids
    assert panel.state_fips_by_geoid["15003"] == 15
    # Lookup
    val = panel.get("15003", "B19013_001E", 2020)
    assert val is not None and val > 0


def test_panel_index_skips_5yr_obs():
    """5-year vintages should not appear in PanelIndex (only 1y signals)."""
    obs_list = [
        _obs("15003", "B19013_001E", 2022, 80000.0, vintage="1y"),
        _obs("15003", "B19013_001E", 2024, 95000.0, vintage="5y"),
    ]
    panel = build_panel_index({("15003", "B19013_001E"): obs_list})
    assert panel.get("15003", "B19013_001E", 2022) == 80000.0
    # 5-year sits at year=2024 with effective midpoint=2022, but we filter
    # vintage != "1y" before recording, so this lookup must miss.
    assert panel.get("15003", "B19013_001E", 2024) is None


def test_pop_one_hot_matches_classify_pop():
    """Hot-encoded bucket must agree with strata.classify_pop on every test value."""
    test_pops = [None, -1, 0, 49_999, 50_000, 199_999, 200_000, 999_999, 1_000_000, 5_000_000]
    bucket_names = ("small", "medium", "large", "xlarge")
    for p in test_pops:
        oh = _pop_one_hot(p)
        cls = classify_pop(p)
        if cls is None:
            assert oh == (0.0, 0.0, 0.0, 0.0)
        else:
            idx = bucket_names.index(cls)
            assert oh[idx] == 1.0
            assert sum(oh) == 1.0


def test_feature_spec_deterministic_order():
    series = _toy_series()
    panel = build_panel_index(series)
    spec = make_feature_spec("B19013_001E", panel)
    cols = spec.column_names
    # First few are core
    assert cols[0] == "log_lag_0"
    assert cols[1] == "log_lag_1"
    # Cross-indicator columns include the *other* indicator only
    assert "x_B01002_001E" in cols
    assert "x_B19013_001E" not in cols
    assert cols[-1] == "horizon"


def test_make_training_rows_walk_forward_cutoff():
    """Training rows must respect the cutoff: target_year ≤ cutoff_year."""
    series = _toy_series()
    panel = build_panel_index(series)
    pops = {"15003": 1_000_000, "15007": 200_000, "15001": 100_000}
    matrix = make_training_rows(
        panel, pops, "B19013_001E", cutoff_year=2020, horizons=(1, 2),
    )
    # Every row must have target_year ≤ 2020
    for (_geoid, src_anchor, target_year, _h) in matrix.meta:
        assert target_year <= 2020
        assert src_anchor < target_year
    # Spec is set
    assert matrix.spec is not None
    assert matrix.spec.target_indicator == "B19013_001E"
    # All rows should have the same feature count
    assert all(len(r) == matrix.spec.n_features for r in matrix.X)
    # Targets are log-growth (small floats, positive for our 3% growth)
    for y in matrix.y:
        assert -0.5 < y < 0.5


def test_make_training_rows_emits_nan_for_missing_lags():
    """When anchor-2 is unobserved, log_lag_2 must be NaN; the row must still emit."""
    obs_list = []
    for year in range(2020, 2023):
        obs_list.append(_obs("15003", "B19013_001E", year, 80000.0 * (1.02 ** (year - 2020))))
    series = {("15003", "B19013_001E"): obs_list}
    panel = build_panel_index(series)
    matrix = make_training_rows(
        panel, {"15003": 1_000_000}, "B19013_001E",
        cutoff_year=2022, horizons=(1,),
    )
    # First valid row will have src_anchor=2021 (need 2020 as lag-1)
    # log_lag_2 must be NaN since 2019 is missing
    row = matrix.X[0]
    spec = matrix.spec
    log_lag_2_idx = spec.column_names.index("log_lag_2")
    diff_2_idx = spec.column_names.index("diff_2")
    assert math.isnan(row[log_lag_2_idx])
    assert math.isnan(row[diff_2_idx])
    # log_lag_0 and log_lag_1 should be finite
    assert math.isfinite(row[0])
    assert math.isfinite(row[1])


def test_make_inference_row_rejects_when_anchor_missing():
    series = _toy_series()
    panel = build_panel_index(series)
    spec = make_feature_spec("B19013_001E", panel)
    # No observation at anchor=2030 — should return None
    row = make_inference_row(
        panel=panel, populations={"15003": 1_000_000},
        geoid="15003", target_indicator="B19013_001E",
        anchor_year=2030, horizon=1, spec=spec,
    )
    assert row is None


def test_make_inference_row_returns_full_feature_vector():
    series = _toy_series()
    panel = build_panel_index(series)
    spec = make_feature_spec("B19013_001E", panel)
    row = make_inference_row(
        panel=panel, populations={"15003": 1_000_000, "15007": 200_000, "15001": 100_000},
        geoid="15003", target_indicator="B19013_001E",
        anchor_year=2022, horizon=2, spec=spec,
    )
    assert row is not None
    assert len(row) == spec.n_features
    # Horizon column is the last
    assert row[-1] == 2.0


def test_make_inference_row_rejects_indicator_mismatch():
    series = _toy_series()
    panel = build_panel_index(series)
    spec = make_feature_spec("B19013_001E", panel)
    with pytest.raises(ValueError):
        make_inference_row(
            panel=panel, populations={"15003": 1_000_000},
            geoid="15003", target_indicator="B01002_001E",
            anchor_year=2022, horizon=1, spec=spec,
        )
