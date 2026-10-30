"""Tests for Phase D: Kalman state-space filter and projection."""
from __future__ import annotations

import math

import numpy as np
import pytest
from census_forecaster.kalman.filter import (
    H_GROWTH,
    H_LEVEL,
    PHI_DEFAULT,
    KalmanState,
    kalman_predict,
    kalman_update,
    make_process_noise,
    make_transition,
)
from census_forecaster.kalman.project import METHOD_NAME, project_kalman
from census_forecaster.models import AcsObservation, ForecastPoint

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _obs(geoid: str, indicator: str, year: int, estimate: float, moe: float = 1000.0):
    return AcsObservation(
        estimate=estimate, moe=moe, year=year, vintage="1y",
        geoid=geoid, indicator=indicator,
    )


def _flat_series(n: int = 10, base: float = 50_000.0):
    """Flat 1y series 2010..2010+n-1 for geoid '15003', indicator 'B19013_001E'."""
    return [_obs("15003", "B19013_001E", 2010 + i, base) for i in range(n)]


def _growing_series(n: int = 10, base: float = 50_000.0, rate: float = 0.03):
    return [
        _obs("15003", "B19013_001E", 2010 + i, base * (1 + rate) ** i)
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# filter.py — predict / update math
# ---------------------------------------------------------------------------

class TestKalmanPredict:
    def test_state_advances_year(self):
        F = make_transition()
        Q = make_process_noise()
        s = KalmanState(mean=np.array([1.0, 0.0]), cov=np.eye(2), year=2020)
        s2 = kalman_predict(s, F, Q)
        assert s2.year == 2021

    def test_level_advances_by_growth(self):
        F = make_transition(phi=1.0)   # no damping for clean check
        Q = make_process_noise(q_level=0, q_growth=0)
        s = KalmanState(mean=np.array([10.0, 0.5]), cov=np.eye(2) * 1e-8, year=2020)
        s2 = kalman_predict(s, F, Q)
        assert abs(s2.mean[0] - 10.5) < 1e-10  # level += growth
        assert abs(s2.mean[1] - 0.5) < 1e-10   # growth unchanged (phi=1)

    def test_growth_damps_with_phi(self):
        phi = 0.85
        F = make_transition(phi)
        Q = make_process_noise(0, 0)
        s = KalmanState(mean=np.array([0.0, 0.1]), cov=np.eye(2) * 1e-8, year=2020)
        s2 = kalman_predict(s, F, Q)
        assert abs(s2.mean[1] - 0.085) < 1e-10

    def test_covariance_grows_with_process_noise(self):
        F = make_transition()
        Q = make_process_noise(q_level=0.01, q_growth=0.01)
        s = KalmanState(mean=np.zeros(2), cov=np.eye(2) * 0.01, year=2020)
        s2 = kalman_predict(s, F, Q)
        assert s2.cov[0, 0] > s.cov[0, 0]


class TestKalmanUpdate:
    def test_level_update_pulls_mean_toward_observation(self):
        s = KalmanState(
            mean=np.array([10.0, 0.0]),
            cov=np.diag([1.0, 1.0]),  # moderate uncertainty
            year=2020,
        )
        # Observe log_level = 11 (above prior mean of 10)
        s2 = kalman_update(s, 11.0, H_LEVEL, R=0.1)
        assert s2.mean[0] > 10.0  # pulled toward 11
        assert s2.mean[0] < 11.0  # but not all the way

    def test_growth_update_only_shifts_growth_state(self):
        s = KalmanState(
            mean=np.array([10.0, 0.0]),
            cov=np.diag([0.001, 1.0]),  # level very certain, growth uncertain
            year=2020,
        )
        s2 = kalman_update(s, 0.05, H_GROWTH, R=0.01)
        assert abs(s2.mean[0] - 10.0) < 1e-3   # level barely moved
        assert abs(s2.mean[1] - 0.05) < 0.05   # growth moved toward 0.05

    def test_covariance_shrinks_after_update(self):
        s = KalmanState(mean=np.zeros(2), cov=np.eye(2), year=2020)
        s2 = kalman_update(s, 0.0, H_LEVEL, R=0.1)
        assert s2.cov[0, 0] < s.cov[0, 0]

    def test_joseph_form_stays_positive_definite(self):
        s = KalmanState(mean=np.zeros(2), cov=np.eye(2), year=2020)
        for _ in range(20):
            s = kalman_update(s, float(np.random.randn()), H_LEVEL, R=0.1)
            eigvals = np.linalg.eigvalsh(s.cov)
            assert all(ev >= -1e-12 for ev in eigvals)

    def test_zero_innovation_cov_returns_unchanged_state(self):
        s = KalmanState(mean=np.ones(2), cov=np.zeros((2, 2)), year=2020)
        s2 = kalman_update(s, 99.0, H_LEVEL, R=0.0)
        assert np.allclose(s2.mean, s.mean)


class TestTransitionMatrix:
    def test_default_phi(self):
        F = make_transition()
        assert F[0, 1] == 1.0
        assert abs(F[1, 1] - PHI_DEFAULT) < 1e-12

    def test_phi_one_is_random_walk(self):
        F = make_transition(phi=1.0)
        assert F[1, 1] == 1.0


# ---------------------------------------------------------------------------
# project.py — project_kalman
# ---------------------------------------------------------------------------

class TestProjectKalmanBasic:
    def test_returns_forecast_point_for_valid_series(self):
        obs = _flat_series(8)
        fp = project_kalman(obs, target_year=2022, end_year=2019)
        assert fp is not None
        assert isinstance(fp, ForecastPoint)

    def test_method_name(self):
        fp = project_kalman(_flat_series(5), target_year=2018, end_year=2014)
        assert fp is not None
        assert fp.method == METHOD_NAME

    def test_geoid_and_indicator_preserved(self):
        obs = _flat_series(5)
        fp = project_kalman(obs, target_year=2018, end_year=2014)
        assert fp is not None
        assert fp.geoid == "15003"
        assert fp.indicator == "B19013_001E"

    def test_horizon_correct(self):
        obs = _flat_series(5)   # ends 2014
        fp = project_kalman(obs, target_year=2017, end_year=2014)
        assert fp is not None
        assert fp.horizon == 3

    def test_default_end_year_is_usable(self):
        """Omitting end_year must work. The default used to be
        max(effective_year(o)) — a float — which range() then rejected with
        TypeError. It stayed latent for months because every caller (and every
        test above) passed an explicit int; this pins the default path itself.
        """
        obs = _flat_series(6)   # ends 2015
        fp_default = project_kalman(obs, target_year=2018)
        fp_explicit = project_kalman(obs, target_year=2018, end_year=2015)
        assert fp_default is not None
        assert fp_default.point == pytest.approx(fp_explicit.point)
        assert fp_default.horizon == fp_explicit.horizon

    def test_point_within_10pct_annual_rate_cap(self):
        # Anchors may pull growth up from a flat series, but never beyond the 10%/yr cap.
        obs = _flat_series(8, base=60_000.0)
        fp = project_kalman(obs, target_year=2020, end_year=2017)
        assert fp is not None
        assert fp.point > 0
        # h=3 at 10%/yr cap ≈ 79860; at -10%/yr floor ≈ 43740
        assert fp.point < 60_000.0 * (1.10 ** 3)
        assert fp.point > 60_000.0 * (0.90 ** 3)

    def test_se_total_positive_and_finite(self):
        fp = project_kalman(_flat_series(6), target_year=2019, end_year=2015)
        assert fp is not None
        assert fp.se_total > 0
        assert math.isfinite(fp.se_total)

    def test_ci_brackets_point(self):
        fp = project_kalman(_flat_series(6), target_year=2019, end_year=2015)
        assert fp is not None
        assert fp.ci90_low < fp.point < fp.ci90_high

    def test_returns_none_for_empty_series(self):
        assert project_kalman([], target_year=2022) is None

    def test_returns_none_for_negative_target(self):
        obs = _flat_series(5)
        # target_year before end_year
        fp = project_kalman(obs, target_year=2010, end_year=2014)
        assert fp is None

    def test_single_observation_still_projects(self):
        obs = [_obs("15003", "B19013_001E", 2015, 50_000.0)]
        fp = project_kalman(obs, target_year=2018, end_year=2015)
        assert fp is not None
        assert fp.horizon == 3

    def test_growing_series_projects_above_last(self):
        obs = _growing_series(8, base=50_000.0, rate=0.04)
        fp = project_kalman(obs, target_year=2021, end_year=2017)
        assert fp is not None
        last_est = obs[7].estimate   # 2017 value
        assert fp.point > last_est   # Kalman should track the upward trend


class TestProjectKalmanUncertainty:
    def test_longer_horizon_wider_ci(self):
        obs = _flat_series(8)
        fp1 = project_kalman(obs, target_year=2015, end_year=2014)
        fp5 = project_kalman(obs, target_year=2019, end_year=2014)
        assert fp1 is not None and fp5 is not None
        assert fp5.se_total > fp1.se_total

    def test_wider_moe_gives_wider_ci(self):
        obs_tight = _flat_series(6)
        obs_wide = [_obs("15003", "B19013_001E", 2010 + i, 50_000.0, moe=10_000.0)
                    for i in range(6)]
        fp_t = project_kalman(obs_tight, target_year=2018, end_year=2015)
        fp_w = project_kalman(obs_wide, target_year=2018, end_year=2015)
        assert fp_t is not None and fp_w is not None
        assert fp_w.se_sample >= fp_t.se_sample
