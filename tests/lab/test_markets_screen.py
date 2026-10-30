"""Tests for markets/screen.py — Granger, xcorr, BH, screen wiring."""
from __future__ import annotations

import math
import random

import pytest
from lab.markets.screen import (
    benjamini_hochberg,
    cross_correlation_lead,
    granger_f_test,
    month_index,
    run_screen,
    series_to_monthly_dict,
    transform_series,
)


def _white_noise(n, seed, mu=0.0, sigma=1.0, start=(2005, 1)):
    rng = random.Random(seed)
    m0 = month_index(*start)
    return {m0 + i: rng.gauss(mu, sigma) for i in range(n)}


# ---------------------------------------------------------------------------
# Plumbing
# ---------------------------------------------------------------------------

def test_series_to_monthly_dict():
    rows = [{"year": 2020, "period": "M01", "value": 3.5},
            {"year": 2020, "period": "M13", "value": 99.0},   # annual-avg
            {"year": 2020, "period": "M02", "value": 3.6}]
    d = series_to_monthly_dict(rows)
    assert d == {month_index(2020, 1): 3.5, month_index(2020, 2): 3.6}


def test_transform_log_diff_handles_bimonthly_gaps():
    # Bimonthly prints (odd months) — per-month rate over gap of 2.
    m0 = month_index(2020, 1)
    values = {m0: 100.0, m0 + 2: 102.0, m0 + 4: 104.04}
    out = transform_series(values, "log_diff")
    assert out[m0 + 2] == pytest.approx(math.log(1.02) / 2)
    assert out[m0 + 4] == pytest.approx(math.log(1.02) / 2)


def test_transform_diff_skips_long_gaps():
    m0 = month_index(2020, 1)
    values = {m0: 5.0, m0 + 1: 5.5, m0 + 8: 9.9}
    out = transform_series(values, "diff")
    assert m0 + 1 in out and m0 + 8 not in out


def test_transform_mom12_exact_endpoints_only():
    m0 = month_index(2020, 1)
    values = {m0: 100.0, m0 + 12: 110.0, m0 + 23: 120.0}
    out = transform_series(values, "mom12")
    assert out == {m0 + 12: pytest.approx(math.log(1.1))}


# ---------------------------------------------------------------------------
# Cross-correlation
# ---------------------------------------------------------------------------

def test_xcorr_recovers_known_six_month_lead():
    rng = random.Random(42)
    m0 = month_index(2005, 1)
    x = {m0 + i: rng.gauss(0, 1) for i in range(200)}
    # y responds to x six months earlier, plus small noise.
    y = {m: 0.9 * x[m - 6] + rng.gauss(0, 0.3)
         for m in x if (m - 6) in x}
    corrs = cross_correlation_lead(x, y, max_lead=12)
    best = max(corrs, key=lambda c: abs(c.r))
    assert best.lead == 6
    assert best.r > 0.8


def test_xcorr_min_n_guard():
    x = _white_noise(10, seed=1)
    y = _white_noise(10, seed=2)
    assert cross_correlation_lead(x, y, max_lead=3, min_n=24) == []


# ---------------------------------------------------------------------------
# Granger F-test
# ---------------------------------------------------------------------------

def test_granger_detects_synthetic_var_lead():
    """y_t = 0.3·y_{t-1} + 0.8·x_{t-3} + ε  → x Granger-causes y."""
    rng = random.Random(7)
    m0 = month_index(2005, 1)
    n = 240
    x = {m0 + i: rng.gauss(0, 1) for i in range(n)}
    y: dict[int, float] = {m0: 0.0, m0 + 1: 0.0, m0 + 2: 0.0}
    for i in range(3, n):
        m = m0 + i
        y[m] = 0.3 * y[m - 1] + 0.8 * x[m - 3] + rng.gauss(0, 0.5)
    g = granger_f_test(y, x, lags=3)
    assert g is not None
    assert g.p_value < 1e-6


def test_granger_null_is_not_significant():
    """Independent white noise → p should not be tiny (single draw)."""
    g = granger_f_test(_white_noise(240, seed=10),
                       _white_noise(240, seed=20), lags=3)
    assert g is not None
    assert g.p_value > 0.001


def test_granger_direction_matters():
    rng = random.Random(9)
    m0 = month_index(2005, 1)
    n = 240
    x = {m0 + i: rng.gauss(0, 1) for i in range(n)}
    y = {m0 + i: 0.0 for i in range(3)}
    for i in range(3, n):
        m = m0 + i
        y[m] = 0.8 * x[m - 3] + rng.gauss(0, 0.5)
    forward = granger_f_test(y, x, lags=3)     # x → y: strong
    reverse = granger_f_test(x, y, lags=3)     # y → x: nothing
    assert forward.p_value < 1e-6
    assert reverse.p_value > forward.p_value * 100


def test_granger_min_nobs_guard_returns_none():
    assert granger_f_test(_white_noise(30, seed=1),
                          _white_noise(30, seed=2), lags=3) is None


# ---------------------------------------------------------------------------
# Benjamini–Hochberg (hand-computed vector)
# ---------------------------------------------------------------------------

def test_bh_hand_computed():
    # Classic example: n=5, q=0.10. Sorted ps: .005 .009 .05 .30 .90
    # thresholds: .02 .04 .06 .08 .10 → largest rank with p<=thr is 3.
    pvals = [0.30, 0.005, 0.90, 0.009, 0.05]
    assert benjamini_hochberg(pvals, q=0.10) == [False, True, False, True, True]


def test_bh_none_pass():
    assert benjamini_hochberg([0.5, 0.9, 0.7], q=0.10) == [False] * 3


def test_bh_all_pass():
    assert benjamini_hochberg([0.001, 0.002], q=0.10) == [True, True]


def test_bh_empty():
    assert benjamini_hochberg([], q=0.10) == []


def test_bh_white_noise_rarely_passes():
    """Under the global null, BH should almost never fire.

    50 independent screens of 12 null tests each; allow a small number
    of false-positive screens (BH controls FDR, not FWER).
    """
    n_screens_with_pass = 0
    for seed in range(50):
        rng = random.Random(seed)
        # p-values of true nulls are U(0,1)
        pvals = [rng.random() for _ in range(12)]
        if any(benjamini_hochberg(pvals, q=0.10)):
            n_screens_with_pass += 1
    assert n_screens_with_pass <= 10


# ---------------------------------------------------------------------------
# run_screen wiring
# ---------------------------------------------------------------------------

def _levels_from_returns(returns: dict[int, float], base=100.0):
    levels, level = {}, base
    for m in sorted(returns):
        level *= math.exp(returns[m])
        levels[m] = level
    return levels


def test_run_screen_finds_planted_relationship_and_dedups_noise():
    rng = random.Random(3)
    m0 = month_index(2005, 1)
    n = 250

    # SPY returns are white noise; HI unemployment responds to SPY
    # returns 3 months back (negative beta: rallies → lower unemployment).
    spy_ret = {m0 + i: rng.gauss(0.005, 0.03) for i in range(n)}
    unemp = {m0: 5.0, m0 + 1: 5.0, m0 + 2: 5.0}
    for i in range(3, n):
        m = m0 + i
        unemp[m] = (unemp[m - 1]
                    - 8.0 * (spy_ret[m - 3] - 0.005)
                    + rng.gauss(0, 0.05))

    tickers = {"SPY": _levels_from_returns(spy_ret),
               "QQQ": _levels_from_returns(
                   {m0 + i: rng.gauss(0.005, 0.04) for i in range(n)})}
    targets = {"HI_UNEMPLOYMENT": unemp}

    report = run_screen(
        tickers, targets,
        pairs=(("SPY", "HI_UNEMPLOYMENT"), ("QQQ", "HI_UNEMPLOYMENT")),
        lags=(3,), q=0.10,
    )
    assert report.n_tests == 2
    spy = next(c for c in report.candidates
               if c.ticker == "SPY" and c.transform == "log_return")
    qqq = next(c for c in report.candidates
               if c.ticker == "QQQ" and c.transform == "log_return")
    assert spy.bh_pass, f"planted signal missed (p={spy.granger.p_value})"
    assert spy.granger.p_value < qqq.granger.p_value


def test_run_screen_missing_target_is_noted_not_crashed():
    tickers = {"SPY": _levels_from_returns(_white_noise(100, 1, 0.005, 0.03))}
    report = run_screen(tickers, {}, pairs=(("SPY", "HONOLULU_ZORI"),))
    assert report.n_tests == 0
    assert any("unavailable" in c.note for c in report.candidates)


def test_run_screen_exclude_2020_drops_those_months():
    rng = random.Random(4)
    m0 = month_index(2018, 1)
    ret = {m0 + i: rng.gauss(0.005, 0.03) for i in range(60)}
    tickers = {"SPY": _levels_from_returns(ret)}
    unemp = {m0 + i: 4.0 + rng.gauss(0, 0.1) for i in range(60)}
    base = run_screen(tickers, {"HI_UNEMPLOYMENT": unemp},
                      pairs=(("SPY", "HI_UNEMPLOYMENT"),), lags=(3,))
    excl = run_screen(tickers, {"HI_UNEMPLOYMENT": unemp},
                      pairs=(("SPY", "HI_UNEMPLOYMENT"),), lags=(3,),
                      exclude_2020=True)
    n_base = [c.granger.nobs for c in base.candidates if c.granger]
    n_excl = [c.granger.nobs for c in excl.candidates if c.granger]
    if n_base and n_excl:  # both had enough obs
        assert n_excl[0] < n_base[0]
    assert excl.exclude_2020


def test_run_screen_mom12_never_gets_granger():
    tickers = {"SPY": _levels_from_returns(
        _white_noise(200, 5, 0.005, 0.03))}
    unemp = _white_noise(200, 6, 4.0, 0.1)
    report = run_screen(tickers, {"HI_UNEMPLOYMENT": unemp},
                        pairs=(("SPY", "HI_UNEMPLOYMENT"),))
    mom_rows = [c for c in report.candidates if c.transform == "mom12"]
    assert mom_rows
    assert all(c.granger is None for c in mom_rows)
    assert all("descriptive" in c.note for c in mom_rows)
