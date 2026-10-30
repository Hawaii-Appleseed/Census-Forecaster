"""Tests for markets/trend.py — φ discipline + band calibration."""
from __future__ import annotations

import math
import random
from datetime import date

import pytest

from census_forecaster.bls.projection import PROJ_DAMPING
from lab.markets.client import MonthlyBar
from lab.markets.trend import (
    calibrate_band_multiplier,
    forecast_ticker,
)


def _bars_from_prices(prices, start=(2010, 1)):
    year, month = start
    out = []
    for p in prices:
        out.append(MonthlyBar(year=year, month=month, adj_close=p))
        month += 1
        if month == 13:
            year, month = year + 1, 1
    return out


def _gbm_prices(n, *, mu=0.005, sigma=0.04, seed=7, s0=100.0):
    rng = random.Random(seed)
    prices, p = [s0], s0
    for _ in range(n - 1):
        p *= math.exp(rng.gauss(mu, sigma))
        prices.append(p)
    return prices


# ---------------------------------------------------------------------------
# φ discipline
# ---------------------------------------------------------------------------

def test_default_phi_is_the_repo_monthly_constant():
    """forecast_ticker must inherit φ=0.92/month from bls.projection —
    never a locally invented damping constant (CLAUDE.md hard rule)."""
    assert PROJ_DAMPING == 0.92
    import inspect

    from lab.markets import trend
    sig = inspect.signature(trend.forecast_ticker)
    assert sig.parameters["phi"].default is PROJ_DAMPING


def test_phi_passthrough_changes_projection():
    bars = _bars_from_prices([100 * 1.02 ** i for i in range(48)])
    target = date(2014, 6, 28)
    damped = forecast_ticker(bars, target, band_multiplier=1.645)
    undamped = forecast_ticker(bars, target, phi=1.0, band_multiplier=1.645)
    # Positive trend: undamped compounding must project higher.
    assert undamped.value > damped.value


# ---------------------------------------------------------------------------
# Forecast mechanics
# ---------------------------------------------------------------------------

def test_forecast_bands_widen_with_horizon():
    bars = _bars_from_prices(_gbm_prices(80))
    last = bars[-1]
    fc3 = forecast_ticker(bars, date(last.year, last.month, 28))
    # horizon 0 → degenerate band at the last value
    assert fc3.horizon_months == 0
    assert fc3.lo90 == pytest.approx(fc3.hi90)

    short = forecast_ticker(bars, date(2017, 2, 28))
    long = forecast_ticker(bars, date(2017, 11, 28))
    assert long.horizon_months > short.horizon_months
    assert (long.hi90 / long.lo90) > (short.hi90 / short.lo90)


def test_forecast_empty_series_raises():
    with pytest.raises(ValueError, match="empty"):
        forecast_ticker([], date(2025, 1, 28))


def test_forecast_deterministic():
    bars = _bars_from_prices(_gbm_prices(60, seed=11))
    a = forecast_ticker(bars, date(2015, 6, 28), band_multiplier=2.0)
    b = forecast_ticker(bars, date(2015, 6, 28), band_multiplier=2.0)
    assert a == b


# ---------------------------------------------------------------------------
# Band calibration — repo PI discipline: empirical coverage, not analytic
# ---------------------------------------------------------------------------

def test_calibrated_band_coverage_on_gbm_within_gate():
    """On a synthetic GBM the calibrated band must actually cover ~90%
    of held-out actuals — the ship-gate range [85%, 95%]."""
    prices = _gbm_prices(240, seed=3)
    bars = _bars_from_prices(prices)
    z = calibrate_band_multiplier(bars)
    assert z is not None and z > 0

    # Out-of-sample-style check over the same walk-forward grid.
    hits = total = 0
    for t in range(36, len(bars) - 12):
        train = bars[:t]
        for h in (3, 6, 12):
            actual = bars[t + h - 1]
            months = ((actual.year * 12 + actual.month)
                      - (train[-1].year * 12 + train[-1].month))
            if months != h:
                continue
            fc = forecast_ticker(
                train, date(actual.year, actual.month, 28),
                band_multiplier=z)
            total += 1
            if fc.lo90 <= actual.adj_close <= fc.hi90:
                hits += 1
    coverage = hits / total
    assert 0.85 <= coverage <= 0.95, f"coverage {coverage:.3f} outside gate"


def test_calibrate_returns_none_when_too_short():
    bars = _bars_from_prices(_gbm_prices(40))
    # 40 bars, min_train=36, max horizon 12 → ~0 usable anchors.
    assert calibrate_band_multiplier(bars) is None


def test_calibrate_deterministic():
    bars = _bars_from_prices(_gbm_prices(150, seed=5))
    assert calibrate_band_multiplier(bars) == calibrate_band_multiplier(bars)
