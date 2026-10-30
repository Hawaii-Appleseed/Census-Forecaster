"""Project a single ACS series forward using the Kalman state-space filter.

State vector: x = [log_level, log_growth_rate]

Observation models:
  - ACS estimate  → H = [1, 0],  y = log(estimate),     R = (se/estimate)²
  - Anchor rate   → H = [0, 1],  y = combined_log_rate,  R = se_log_rate²

The filter fuses annual ACS level observations with multi-source anchor
growth-rate observations.  When no anchor sources cover an indicator the
filter degenerates to a pure state-space trend (still useful for
uncertainty propagation).
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from common.models import AcsObservation, ForecastPoint
from common.moe import ci_from_se, combine_se, moe_to_se

from ..acs.anchors import combined_anchor_rate
from ..backtest.acs import effective_year
from .filter import (
    H_GROWTH,
    H_LEVEL,
    PHI_DEFAULT,
    KalmanState,
    kalman_predict,
    kalman_update,
    make_process_noise,
    make_transition,
)

METHOD_NAME: str = "kalman_state_space"

# Process noise defaults
_Q_LEVEL: float = 1e-6          # nearly no direct level noise (ACS observes it)
_Q_GROWTH: float = (0.02) ** 2  # ~2% annual growth rate stochasticity

# Diffuse initialisation variances
_INIT_VAR_LEVEL: float = 1.0    # ±100% log-level uncertainty at start
_INIT_VAR_GROWTH: float = 0.25  # ±50% initial growth rate uncertainty (2σ)


def _obs_r(obs: AcsObservation) -> float:
    """Log-space measurement noise variance for one ACS observation."""
    se = moe_to_se(obs.moe)
    if obs.estimate > 0 and math.isfinite(se) and se > 0:
        return (se / obs.estimate) ** 2
    return 0.01  # fallback: ~10% CV


def project_kalman(
    series_observations: Sequence[AcsObservation],
    target_year: int,
    end_year: int | None = None,
    calibration: dict | None = None,
    phi: float = PHI_DEFAULT,
    geoid: str | None = None,
) -> ForecastPoint | None:
    """Kalman-filter projection of one (geoid, indicator) series.

    Parameters
    ----------
    series_observations:
        ACS observations up to (and including) end_year.  May be a
        truncated view for back-test fold evaluation.
    target_year:
        Year to project forward to.
    end_year:
        Last year of available data.  Defaults to max(effective_year).
    calibration:
        Full calibration dict (used to weight anchor sources via
        calibration["rmse_by_indicator_source"]).
    phi:
        Growth-damping coefficient (default 0.85, same as damped trend).
    geoid:
        Override the geoid for county-level anchor look-ups.
    """
    obs_1y = sorted(
        (o for o in series_observations if o.estimate > 0 and o.vintage == "1y"),
        key=effective_year,
    )
    if not obs_1y:
        return None

    if end_year is None:
        # effective_year returns a float (5y vintages sit at year-2.0); the
        # filter loop and the h-step predictor below both feed this to
        # range(), so it MUST be an int. Without the coercion the default
        # branch raises TypeError — latent until now because every in-repo
        # caller (ensemble.project_ensemble_multi) passes an explicit int.
        end_year = int(round(max(effective_year(o) for o in obs_1y)))

    # Trim to ≤ end_year (caller may have already done this).
    obs_1y = [o for o in obs_1y if effective_year(o) <= end_year]
    if not obs_1y:
        return None

    h = int(target_year - end_year)
    if h < 0:
        return None

    first = obs_1y[0]
    first_year = int(effective_year(first))
    indicator = first.indicator
    if geoid is None:
        geoid = first.geoid

    # ── Initialise state ─────────────────────────────────────────────────
    init_log_level = math.log(first.estimate)

    # Seed growth estimate from first two observations if possible.
    if len(obs_1y) >= 2:
        second = obs_1y[1]
        dy = int(effective_year(second)) - first_year
        if dy > 0 and second.estimate > 0:
            init_log_growth = (math.log(second.estimate) - init_log_level) / dy
        else:
            init_log_growth = 0.0
    else:
        init_log_growth = 0.0

    state = KalmanState(
        mean=np.array([init_log_level, init_log_growth]),
        cov=np.diag([_INIT_VAR_LEVEL, _INIT_VAR_GROWTH]).astype(float),
        year=first_year,
    )

    F = make_transition(phi)
    Q = make_process_noise(_Q_LEVEL, _Q_GROWTH)

    # Assimilate the first observation at its year.
    state = kalman_update(state, init_log_level, H_LEVEL, _obs_r(first))

    per_source_calib = (calibration or {}).get("rmse_by_indicator_source")
    obs_by_year = {int(effective_year(o)): o for o in obs_1y}

    # ── Forward filter from year after first through end_year ────────────
    for year in range(first_year + 1, end_year + 1):
        state = kalman_predict(state, F, Q)

        # ACS level update.
        if year in obs_by_year:
            o = obs_by_year[year]
            state = kalman_update(state, math.log(o.estimate), H_LEVEL, _obs_r(o))

        # Anchor growth-rate update.
        anchor = combined_anchor_rate(
            indicator=indicator,
            end_year=year,
            calibration=per_source_calib,
            calibration_horizon=1,
            geoid=geoid,
        )
        if anchor is not None and math.isfinite(anchor.point_log_rate):
            r_anchor = max(anchor.se_log_rate ** 2, 1e-6)
            state = kalman_update(state, anchor.point_log_rate, H_GROWTH, r_anchor)

    if not (math.isfinite(state.mean[0]) and math.isfinite(state.cov[0, 0])):
        return None

    # ── Predict h steps forward from posterior at end_year ───────────────
    pred = state
    for _ in range(h):
        pred = kalman_predict(pred, F, Q)

    log_level_hat = float(pred.mean[0])
    log_level_var = max(float(pred.cov[0, 0]), 0.0)

    point = math.exp(log_level_hat)

    # Forecast SE from posterior level variance (delta method, log-normal).
    se_forecast = math.sqrt(log_level_var) * point

    # Sample SE from the most recent ACS observation (carried forward).
    latest = obs_1y[-1]
    rel_se = moe_to_se(latest.moe) / latest.estimate if latest.estimate > 0 else 0.0
    if not math.isfinite(rel_se):
        rel_se = 0.0
    se_sample = rel_se * point

    se_total = combine_se(se_sample, se_forecast)
    ci_lo, ci_hi = ci_from_se(point, se_total)

    return ForecastPoint(
        point=point,
        se_total=se_total,
        se_sample=se_sample,
        se_forecast=se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method=METHOD_NAME,
        target_year=target_year,
        geoid=geoid,
        indicator=indicator,
        horizon=target_year - int(effective_year(latest)),
        notes=(
            f"kalman(phi={phi}, n_obs={len(obs_1y)}, "
            f"end={end_year}, h={h})"
        ),
    )
