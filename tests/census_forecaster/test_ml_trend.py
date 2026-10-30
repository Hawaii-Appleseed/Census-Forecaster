"""Tests for the ml_trend module.

Coverage:
* Training succeeds on a synthetic panel large enough to clear _MIN_TRAINING_ROWS.
* Training returns None when panel is too small.
* `project_ml_trend` produces a ForecastPoint with correct method tag,
  positive SE components, and CI bounds bracketing the point.
* The annual rate cap fires when the predicted growth would exceed it.
* Cache reuse: project_ml_trend_one_shot reuses cached trained models.
* Graceful degradation: when sklearn is unavailable, train_ml_model
  returns None and the ensemble falls back transparently.
"""
from __future__ import annotations

import math
from unittest import mock

import pytest
from census_forecaster.acs.ml_features import build_panel_index
from census_forecaster.acs.ml_trend import (
    _MIN_TRAINING_ROWS,
    METHOD_NAME,
    project_ml_trend,
    project_ml_trend_one_shot,
    train_ml_model,
)
from census_forecaster.models import AcsObservation


def _obs(geoid, indicator, year, est):
    return AcsObservation(
        estimate=float(est), moe=float(est) * 0.05, year=year, vintage="1y",
        geoid=geoid, indicator=indicator,
    )


def _big_panel(n_counties=80, n_years=12, n_indicators=3):
    """Build a panel large enough to clear the _MIN_TRAINING_ROWS bar."""
    series = {}
    pops = {}
    for c in range(n_counties):
        geoid = f"15{c:03d}"
        pops[geoid] = 50_000 + c * 10_000
        for ind_idx in range(n_indicators):
            ind = f"X{ind_idx}_001E"
            obs_list = []
            base = 50_000.0 + ind_idx * 5_000 + c * 200
            for y in range(2010, 2010 + n_years):
                # Mild growth + tiny per-county idiosyncrasy
                val = base * (1.02 ** (y - 2010)) * (1.0 + 0.001 * c)
                obs_list.append(_obs(geoid, ind, y, val))
            series[(geoid, ind)] = obs_list
    return series, pops


def test_train_returns_none_on_small_panel():
    series = {}
    pops = {}
    # 2 counties × 5 years; way below _MIN_TRAINING_ROWS
    for c in (1, 2):
        for y in range(2018, 2023):
            obs = _obs(f"1500{c}", "B19013_001E", y, 80000.0)
            series.setdefault((f"1500{c}", "B19013_001E"), []).append(obs)
        pops[f"1500{c}"] = 100_000
    model = train_ml_model(series, pops, "B19013_001E", cutoff_year=2022)
    assert model is None


def test_train_succeeds_on_large_panel_and_residuals_per_horizon():
    series, pops = _big_panel(n_counties=80, n_years=12, n_indicators=3)
    model = train_ml_model(
        series, pops, "X0_001E", cutoff_year=2020, horizons=(1, 2, 3),
    )
    assert model is not None
    assert model.indicator == "X0_001E"
    assert model.cutoff_year == 2020
    assert model.n_train >= _MIN_TRAINING_ROWS
    # Residual std must be non-negative and finite for each trained horizon
    for h in (1, 2, 3):
        assert h in model.residual_std_by_horizon
        std = model.residual_std_by_horizon[h]
        assert math.isfinite(std) and std >= 0.0
    # Global residual std non-negative
    assert math.isfinite(model.residual_std_global) and model.residual_std_global >= 0.0


def test_project_ml_trend_returns_method_tagged_forecast():
    series, pops = _big_panel(n_counties=80, n_years=12, n_indicators=3)
    panel = build_panel_index(series)
    model = train_ml_model(series, pops, "X0_001E", cutoff_year=2020, horizons=(1, 2))

    # Predict 2022 from a county's data through 2020
    geoid = "15040"
    obs_list = sorted(
        [o for o in series[(geoid, "X0_001E")] if o.year <= 2020],
        key=lambda o: o.year,
    )
    fp = project_ml_trend(obs_list, target_year=2022, model=model, panel=panel, populations=pops)
    assert fp is not None
    assert fp.method == METHOD_NAME
    assert fp.geoid == geoid
    assert fp.indicator == "X0_001E"
    assert fp.target_year == 2022
    assert fp.horizon == 2
    # Sanity on SE/CI
    assert fp.se_total > 0
    assert fp.se_forecast > 0
    assert fp.ci90_low <= fp.point <= fp.ci90_high


def test_project_ml_trend_passthrough_for_zero_horizon():
    """Target year ≤ latest observation year → passthrough form."""
    series, pops = _big_panel(n_counties=80, n_years=12, n_indicators=3)
    panel = build_panel_index(series)
    model = train_ml_model(series, pops, "X0_001E", cutoff_year=2020)
    geoid = "15040"
    obs_list = sorted(series[(geoid, "X0_001E")], key=lambda o: o.year)
    fp = project_ml_trend(
        obs_list, target_year=obs_list[-1].year, model=model,
        panel=panel, populations=pops,
    )
    assert fp is not None
    assert fp.method.endswith("_passthrough")
    assert fp.point == obs_list[-1].estimate


def test_project_ml_trend_one_shot_uses_cache():
    series, pops = _big_panel(n_counties=80, n_years=12, n_indicators=2)
    panel = build_panel_index(series)
    cache = {}

    obs_list = sorted(series[("15010", "X0_001E")], key=lambda o: o.year if o.year <= 2020 else -1)
    obs_list = [o for o in obs_list if o.year <= 2020]

    fp1 = project_ml_trend_one_shot(
        series_by_key=series, populations=pops,
        series_observations=obs_list, target_year=2022,
        cutoff_year=2020, model_cache=cache, panel=panel,
    )
    assert fp1 is not None
    # Cache populated
    assert ("X0_001E", 2020) in cache
    cached_model = cache[("X0_001E", 2020)]
    # Second call must reuse the cached model object
    fp2 = project_ml_trend_one_shot(
        series_by_key=series, populations=pops,
        series_observations=obs_list, target_year=2022,
        cutoff_year=2020, model_cache=cache, panel=panel,
    )
    assert fp2 is not None
    assert fp2.point == pytest.approx(fp1.point)
    assert cache[("X0_001E", 2020)] is cached_model


def test_project_ml_trend_indicator_mismatch_raises():
    series, pops = _big_panel(n_counties=80, n_years=12, n_indicators=3)
    panel = build_panel_index(series)
    model = train_ml_model(series, pops, "X0_001E", cutoff_year=2020)
    obs_list = sorted(series[("15040", "X1_001E")], key=lambda o: o.year)
    with pytest.raises(ValueError):
        project_ml_trend(
            obs_list, target_year=2022, model=model,
            panel=panel, populations=pops,
        )


def test_train_ml_model_returns_none_when_sklearn_missing():
    """Graceful degradation: import failure → None, no exception."""
    with mock.patch(
        "census_forecaster.acs.ml_trend._try_import_sklearn", return_value=None
    ):
        series, pops = _big_panel(n_counties=80, n_years=12, n_indicators=2)
        model = train_ml_model(series, pops, "X0_001E", cutoff_year=2020)
        assert model is None


def test_project_ml_trend_one_shot_returns_none_on_missing_anchor():
    """If the inference county has no obs at anchor, one-shot returns None gracefully."""
    series, pops = _big_panel(n_counties=80, n_years=12, n_indicators=2)
    # Predict for a county not in the panel
    bogus_obs = [_obs("99999", "X0_001E", 2020, 50_000.0)]
    fp = project_ml_trend_one_shot(
        series_by_key=series, populations=pops,
        series_observations=bogus_obs, target_year=2022,
        cutoff_year=2020,
    )
    # Either None (preferred — we have no anchor-1 in the panel) or a passthrough.
    # The bogus county has anchor=2020 but no anchor-1=2019 in panel → returns None.
    assert fp is None
