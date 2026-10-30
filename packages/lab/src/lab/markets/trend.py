"""Tracker-grade ticker trend forecasts.

Equity prices are close to a random walk; the point forecast here is a
*damped-drift* projection (reusing ``bls.projection.project_forward_full``
at the repo's monthly damping φ=0.92 — METHODOLOGY.md §2.3.1, never a new
φ) and the honest content is the *band*: an empirical-volatility interval
``point × exp(±z·σ_m·√h)`` whose multiplier ``z`` is calibrated by
walk-forward pseudo-out-of-sample coverage targeting 90% (repo PI
discipline: empirical quantiles, never analytical z-scores).

These forecasts are context for the tracker report — "where does the
damped trend put SPY in 6 months, within what band" — NOT trading advice
and NOT inputs to the census forecaster (the Phase-3 signals use realized
history only).
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from census_forecaster.bls.projection import PROJ_DAMPING, project_forward_full

from .client import MonthlyBar

# Default band multiplier when a series is too short to calibrate:
# the N(0,1) 90% two-sided quantile. Calibration overrides this with
# the empirical value (typically > 1.645 — equity returns are fat-tailed).
_FALLBACK_Z = 1.645

# Trailing window (months) for the σ_m estimate.
_VOL_WINDOW = 36
_MIN_VOL_OBS = 12

# RiskMetrics-style monthly decay for the EWMA vol option. Chosen by a
# 2026-07 walk-forward bake-off (3,806 pooled forecasts, sequentially
# calibrated 90% multipliers): EWMA λ=0.97 beat the rolling-36 SD on
# interval score (0.6076 vs 0.6152) at identical coverage (0.898), and
# GARCH(1,1) via `arch` LOST to both (0.6277) — monthly cadence gives
# maximum-likelihood vol fitting too few observations, so the extra
# dependency was rejected. See METHODOLOGY "Market signals".
_EWMA_LAMBDA = 0.97


@dataclass(frozen=True)
class TickerForecast:
    """Point + 90% band for one ticker at one horizon."""
    value: float
    lo90: float
    hi90: float
    monthly_vol: float
    band_multiplier: float
    horizon_months: int
    cap_hit: bool


def _monthly_log_returns(bars: Sequence[MonthlyBar]) -> list[float]:
    out = []
    for prev, cur in zip(bars, bars[1:]):
        gap = (cur.year * 12 + cur.month) - (prev.year * 12 + prev.month)
        if gap == 1 and prev.adj_close > 0 and cur.adj_close > 0:
            out.append(math.log(cur.adj_close / prev.adj_close))
    return out


def _monthly_vol(bars: Sequence[MonthlyBar],
                 window: int = _VOL_WINDOW,
                 method: str = "rolling") -> float | None:
    """Monthly σ estimate: 'rolling' (trailing-window sample SD, the
    original default) or 'ewma' (RiskMetrics recursion, λ=0.97 —
    bake-off winner; see `_EWMA_LAMBDA`)."""
    returns = _monthly_log_returns(bars)
    if method == "ewma":
        if len(returns) < _MIN_VOL_OBS:
            return None
        seed = returns[:_MIN_VOL_OBS]
        mean = sum(seed) / len(seed)
        var = sum((r - mean) ** 2 for r in seed) / (len(seed) - 1)
        for r in returns[_MIN_VOL_OBS:]:
            var = _EWMA_LAMBDA * var + (1.0 - _EWMA_LAMBDA) * r * r
        return math.sqrt(var)
    if method != "rolling":
        raise ValueError(f"unknown vol method: {method!r}")
    tail = returns[-window:]
    n = len(tail)
    if n < _MIN_VOL_OBS:
        return None
    mean = sum(tail) / n
    var = sum((r - mean) ** 2 for r in tail) / (n - 1)
    return math.sqrt(var)


def _to_points(bars: Sequence[MonthlyBar]) -> list[dict]:
    return [{"year": b.year, "period": f"M{b.month:02d}", "value": b.adj_close}
            for b in bars]


def forecast_ticker(
    bars: Sequence[MonthlyBar],
    target_date: date,
    *,
    phi: float = PROJ_DAMPING,
    band_multiplier: float | None = None,
    vol_method: str = "rolling",
) -> TickerForecast:
    """Damped-drift point forecast + empirical-vol 90% band.

    ``band_multiplier`` should come from :func:`calibrate_band_multiplier`
    for the same ticker AND the same ``vol_method`` — the multiplier is
    the empirical quantile of errors standardized by that σ, so mixing
    methods mis-scales the band. Falls back to the normal 90% quantile
    when the series is too short to calibrate.
    """
    if not bars:
        raise ValueError("cannot forecast an empty series")
    proj = project_forward_full(_to_points(bars), target_date, phi=phi)
    sigma = _monthly_vol(bars, method=vol_method)
    z = band_multiplier if band_multiplier is not None else _FALLBACK_Z
    if sigma is None or proj.horizon_months <= 0:
        half_width = 0.0
        sigma = sigma or 0.0
    else:
        half_width = z * sigma * math.sqrt(proj.horizon_months)
    return TickerForecast(
        value=proj.value,
        lo90=proj.value * math.exp(-half_width),
        hi90=proj.value * math.exp(half_width),
        monthly_vol=float(sigma),
        band_multiplier=float(z),
        horizon_months=proj.horizon_months,
        cap_hit=proj.cap_fired,
    )


def calibrate_band_multiplier(
    bars: Sequence[MonthlyBar],
    *,
    horizons: tuple[int, ...] = (3, 6, 12),
    min_train: int = 36,
    phi: float = PROJ_DAMPING,
    coverage: float = 0.90,
    vol_method: str = "rolling",
) -> float | None:
    """Walk-forward empirical band multiplier hitting ``coverage``.

    For every pseudo-anchor ``t`` and horizon ``h``: forecast from
    ``bars[:t]`` to the month of ``bars[t+h-1]``, standardise the
    absolute log error by ``σ_m(train)·√h``, and return the empirical
    ``coverage`` quantile of those standardised errors. A band built
    with this multiplier would have covered ``coverage`` of the
    pseudo-out-of-sample actuals — the direct empirical analogue of a
    z-score, robust to fat tails.

    Returns None when the series is too short to produce at least 20
    standardised errors (below that, the quantile is noise).
    """
    bars = list(bars)
    errors: list[float] = []
    max_h = max(horizons)
    for t in range(min_train, len(bars) - max_h + 1):
        train = bars[:t]
        sigma = _monthly_vol(train, method=vol_method)
        if sigma is None or sigma <= 0:
            continue
        for h in horizons:
            actual_bar = bars[t + h - 1]
            # Guard against calendar gaps: the target must actually be
            # h months past the last training bar.
            months_ahead = (
                (actual_bar.year * 12 + actual_bar.month)
                - (train[-1].year * 12 + train[-1].month)
            )
            if months_ahead != h:
                continue
            proj = project_forward_full(
                _to_points(train),
                date(actual_bar.year, actual_bar.month, 28),
                phi=phi,
            )
            if proj.value <= 0 or actual_bar.adj_close <= 0:
                continue
            e = abs(math.log(actual_bar.adj_close / proj.value))
            errors.append(e / (sigma * math.sqrt(h)))
    if len(errors) < 20:
        return None
    errors.sort()
    # Empirical quantile (nearest-rank).
    rank = min(len(errors) - 1, max(0, math.ceil(coverage * len(errors)) - 1))
    return errors[rank]


__all__ = ["TickerForecast", "forecast_ticker", "calibrate_band_multiplier"]
