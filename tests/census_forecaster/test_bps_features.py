"""Tests for Phase C: BPS permit leading-indicator ML features."""
from __future__ import annotations

import math

import pytest
from census_forecaster.acs.ml_features import (
    _BPS_INDICATOR,
    PanelIndex,
    build_panel_index,
    load_county_data,
    make_feature_spec,
    make_training_rows,
)
from census_forecaster.models import AcsObservation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs(geoid: str, indicator: str, year: int, estimate: float) -> AcsObservation:
    return AcsObservation(
        estimate=estimate, moe=1.0, year=year, vintage="1y",
        geoid=geoid, indicator=indicator,
    )


def _make_series(geoid: str, indicator: str, years: range,
                 base: float = 100_000.0, growth: float = 0.03):
    return [(geoid, indicator), [
        _obs(geoid, indicator, y, base * (1 + growth) ** (y - years.start))
        for y in years
    ]]


# ---------------------------------------------------------------------------
# BPS data file
# ---------------------------------------------------------------------------

class TestBpsDataFile:
    def test_load_bps_data_returns_dict_or_none(self):
        result = load_county_data().get("bps")
        assert result is None or isinstance(result, dict)

    def test_bps_file_covers_90_counties(self):
        bps = load_county_data().get("bps")
        if bps is None:
            pytest.skip("bps_permits.json not present")
        assert len(bps) == 90

    def test_bps_values_are_positive_integers(self):
        bps = load_county_data().get("bps")
        if bps is None:
            pytest.skip("bps_permits.json not present")
        for geoid, yr_dict in list(bps.items())[:5]:
            for yr, val in yr_dict.items():
                assert isinstance(val, (int, float)), f"{geoid}/{yr}: {val}"
                assert val >= 0, f"{geoid}/{yr}: {val}"

    def test_bps_covers_calibration_years(self):
        bps = load_county_data().get("bps")
        if bps is None:
            pytest.skip("bps_permits.json not present")
        for geoid in list(bps.keys())[:3]:
            years = set(bps[geoid].keys())
            # Need 2008-2010 to provide lag-2 for anchor_year=2010
            assert 2010 in years, f"{geoid} missing 2010"
            assert 2022 in years, f"{geoid} missing 2022"


# ---------------------------------------------------------------------------
# build_panel_index with county_data
# ---------------------------------------------------------------------------

class TestBuildPanelIndexWithBps:
    def _base_series(self):
        g1, g2 = "15003", "15001"
        ind = "B25077_001E"
        series = {}
        for geoid in (g1, g2):
            years = range(2010, 2024)
            obs_list = [_obs(geoid, ind, y, 500_000 + y * 1000) for y in years]
            series[(geoid, ind)] = obs_list
        return series

    def test_bps_stored_in_estimate_by_key(self):
        series = self._base_series()
        bps = {"15003": {2018: 1500, 2019: 1600, 2020: 800, 2021: 1400, 2022: 1550}}
        panel = build_panel_index(series, county_data={"bps": bps})
        assert panel.get("15003", _BPS_INDICATOR, 2019) == 1600.0
        assert panel.get("15003", _BPS_INDICATOR, 2018) == 1500.0

    def test_bps_not_in_indicators(self):
        series = self._base_series()
        bps = {"15003": {2019: 1200}}
        panel = build_panel_index(series, county_data={"bps": bps})
        assert _BPS_INDICATOR not in panel.indicators

    def test_no_bps_data_panel_still_works(self):
        series = self._base_series()
        panel = build_panel_index(series, county_data=None)
        assert panel.get("15003", _BPS_INDICATOR, 2019) is None

    def test_zero_permits_not_stored(self):
        series = self._base_series()
        bps = {"15003": {2019: 0, 2020: 500}}
        panel = build_panel_index(series, county_data={"bps": bps})
        # Zero treated as missing (log-undefined); positive stored.
        assert panel.get("15003", _BPS_INDICATOR, 2020) == 500.0
        # The zero really is absent — this assertion is what the test name
        # always claimed but never checked. Pre-registry the injection guard
        # was `>= 0`, so the 0 WAS stored (harmlessly: the reader NaNs it
        # anyway). The county registry unified the guard to `> 0`, matching
        # the documented intent; features are bit-identical either way
        # (proved by the golden-row check in the migration).
        assert panel.get("15003", _BPS_INDICATOR, 2019) is None

    def test_missing_geoid_in_bps_still_returns_none(self):
        series = self._base_series()
        bps = {"15003": {2019: 1200}}
        panel = build_panel_index(series, county_data={"bps": bps})
        assert panel.get("15001", _BPS_INDICATOR, 2019) is None


# ---------------------------------------------------------------------------
# Feature spec and row shape
# ---------------------------------------------------------------------------

class TestBpsFeatureColumns:
    def _panel_with_bps(self) -> tuple[PanelIndex, dict]:
        ind = "B25077_001E"
        geoid = "15003"
        series = {
            (geoid, ind): [_obs(geoid, ind, y, 500_000 + y * 1000) for y in range(2010, 2024)],
        }
        bps = {geoid: {y: 1000 + y * 10 for y in range(2008, 2024)}}
        populations = {geoid: 980_000}
        panel = build_panel_index(series, county_data={"bps": bps})
        return panel, populations

    def test_feature_spec_includes_bps_columns(self):
        panel, _ = self._panel_with_bps()
        spec = make_feature_spec("B25077_001E", panel)
        assert "bps_log_lag0" in spec.column_names
        assert "bps_log_lag1" in spec.column_names
        assert "bps_log_lag2" in spec.column_names
        assert "bps_3yr_mean" in spec.column_names

    def test_bps_columns_are_finite_when_data_present(self):
        from census_forecaster.acs.ml_features import _build_row
        panel, populations = self._panel_with_bps()
        spec = make_feature_spec("B25077_001E", panel)
        row = _build_row(panel, populations, "15003", "B25077_001E", 2020, 2, spec)
        assert row is not None
        col_names = list(spec.column_names)
        for bps_col in ("bps_log_lag0", "bps_log_lag1", "bps_log_lag2", "bps_3yr_mean"):
            idx = col_names.index(bps_col)
            assert math.isfinite(row[idx]), f"{bps_col} should be finite when BPS present"

    def test_bps_columns_are_nan_when_no_bps(self):
        from census_forecaster.acs.ml_features import _build_row
        ind = "B25077_001E"
        geoid = "15003"
        series = {(geoid, ind): [_obs(geoid, ind, y, 500_000) for y in range(2010, 2024)]}
        populations = {geoid: 980_000}
        panel = build_panel_index(series, county_data=None)
        spec = make_feature_spec(ind, panel)
        row = _build_row(panel, populations, geoid, ind, 2020, 2, spec)
        assert row is not None
        col_names = list(spec.column_names)
        for bps_col in ("bps_log_lag0", "bps_log_lag1", "bps_log_lag2", "bps_3yr_mean"):
            idx = col_names.index(bps_col)
            assert math.isnan(row[idx]), f"{bps_col} should be NaN when BPS absent"

    def test_bps_3yr_mean_equals_mean_of_finite_lags(self):
        from census_forecaster.acs.ml_features import _build_row
        panel, populations = self._panel_with_bps()
        spec = make_feature_spec("B25077_001E", panel)
        row = _build_row(panel, populations, "15003", "B25077_001E", 2020, 2, spec)
        assert row is not None
        col_names = list(spec.column_names)
        lag0 = row[col_names.index("bps_log_lag0")]
        lag1 = row[col_names.index("bps_log_lag1")]
        lag2 = row[col_names.index("bps_log_lag2")]
        mean = row[col_names.index("bps_3yr_mean")]
        expected = (lag0 + lag1 + lag2) / 3
        assert abs(mean - expected) < 1e-10

    def test_training_matrix_row_count_unchanged(self):
        ind = "B25077_001E"
        geoid = "15003"
        series = {(geoid, ind): [_obs(geoid, ind, y, 500_000 + y * 100) for y in range(2010, 2024)]}
        populations = {geoid: 980_000}
        panel_no_bps = build_panel_index(series, county_data=None)
        panel_with_bps = build_panel_index(series, county_data={"bps": {geoid: {y: 1000 for y in range(2008, 2024)}}})
        mat_no = make_training_rows(panel_no_bps, populations, ind, cutoff_year=2020)
        mat_bps = make_training_rows(panel_with_bps, populations, ind, cutoff_year=2020)
        # Same number of rows — BPS features are NaN-filled, not row-dropping.
        assert len(mat_no.X) == len(mat_bps.X)
        # BPS panel produces wider feature vectors.
        if mat_no.X and mat_bps.X:
            assert len(mat_bps.X[0]) == len(mat_no.X[0])  # same width (BPS cols always present)
