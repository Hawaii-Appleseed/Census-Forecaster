"""Tests for the S2301 mean-reversion model (acs/mean_reversion.py)."""
from __future__ import annotations

import pytest
from census_forecaster.acs.mean_reversion import (
    METHOD_NAME,
    load_laus_values,
    project_mean_reversion,
)
from census_forecaster.models import AcsObservation


def _acs(year, est, geoid="15003", indicator="S2301_C04_001E", moe=0.8):
    return AcsObservation(
        estimate=est, moe=moe, year=year, vintage="1y",
        geoid=geoid, indicator=indicator,
    )


def _laus(geoid="15003", values=None):
    # A county that spiked to 9% and is reverting toward a ~4% mean.
    values = values or {
        2010: 6.0, 2011: 5.5, 2012: 5.0, 2013: 4.5, 2014: 4.2,
        2015: 4.0, 2016: 3.8, 2017: 3.6, 2018: 3.5, 2019: 3.4,
        2020: 9.0, 2021: 6.5, 2022: 4.5,
    }
    return {geoid: values}


class TestProjectMeanReversion:
    def _obs_series(self, end=2022):
        # ACS runs ~0.5pp above LAUS for this county (systematic offset).
        laus = _laus()["15003"]
        return [_acs(y, laus[y] + 0.5) for y in sorted(laus) if y <= end]

    def test_returns_none_for_unsupported_indicator(self):
        obs = [_acs(2020 + i, 100.0, indicator="B19013_001E") for i in range(5)]
        assert project_mean_reversion(obs, target_year=2026) is None

    def test_returns_none_without_laus_history(self):
        obs = self._obs_series()
        fp = project_mean_reversion(
            obs, target_year=2024, end_year=2022,
            laus_values={"15003": {2021: 4.0, 2022: 4.5}},  # too short
        )
        assert fp is None

    def test_returns_none_for_non_future_target(self):
        obs = self._obs_series()
        assert project_mean_reversion(
            obs, target_year=2022, end_year=2022, laus_values=_laus(),
        ) is None

    def test_reverts_toward_mean_from_elevated_state(self):
        # Anchor 2020: state 9.0, long-run mean ≈ 4.5 → the forecast for
        # 2022 must sit strictly between state and mean (plus offset).
        obs = self._obs_series(end=2020)
        fp = project_mean_reversion(
            obs, target_year=2022, end_year=2020, laus_values=_laus(),
        )
        assert fp is not None
        assert fp.method == METHOD_NAME
        laus = _laus()["15003"]
        mu = sum(v for y, v in laus.items() if y <= 2020) / 11
        assert (mu + 0.5) < fp.point < (9.0 + 0.5) + 1e-9

    def test_applies_acs_laus_offset(self):
        # Same LAUS path, ACS shifted +2.0 → forecast shifts by ~+1.5
        # relative to the +0.5-offset series (difference of offsets).
        laus = _laus()
        obs_lo = self._obs_series(end=2019)
        obs_hi = [_acs(o.year, o.estimate + 1.5) for o in obs_lo]
        fp_lo = project_mean_reversion(obs_lo, 2021, end_year=2019, laus_values=laus)
        fp_hi = project_mean_reversion(obs_hi, 2021, end_year=2019, laus_values=laus)
        assert fp_hi.point - fp_lo.point == pytest.approx(1.5, abs=1e-9)

    def test_se_grows_with_horizon(self):
        obs = self._obs_series(end=2019)
        fps = [
            project_mean_reversion(obs, 2019 + h, end_year=2019, laus_values=_laus())
            for h in (1, 2, 3)
        ]
        ses = [fp.se_total for fp in fps]
        assert ses[0] < ses[1] < ses[2]

    def test_point_floor_never_negative(self):
        laus = {"15003": {y: 0.5 for y in range(2010, 2023)}}
        obs = [_acs(y, 0.4) for y in range(2010, 2023)]
        fp = project_mean_reversion(obs, 2024, end_year=2022, laus_values=laus)
        assert fp is not None
        assert fp.point > 0
        assert fp.ci90_low >= 0.0

    def test_bundled_laus_loads_panel_counties(self):
        laus = load_laus_values()
        assert len(laus) >= 80
        assert "15003" in laus
