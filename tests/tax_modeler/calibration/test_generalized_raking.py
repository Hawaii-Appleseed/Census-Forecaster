"""``calibration.generalized_raking``: calmar solves the calibration equations
jointly, respects its bounds, and its feasibility helpers agree with hand
arithmetic."""
from __future__ import annotations

import numpy as np
import pytest

from tax_modeler.calibration.generalized_raking import calmar, feasible_interval, lp_feasible


def _problem(seed: int = 0, n: int = 500):
    rng = np.random.default_rng(seed)
    d = rng.uniform(5, 20, n)
    cell = rng.integers(0, 3, n)
    status = rng.integers(0, 2, n)
    tax = rng.gamma(2.0, 100.0, n) * (cell + 1)
    X = np.column_stack([
        (cell == 0), (cell == 1), (cell == 2),      # count cells
        (status == 0), (status == 1),               # a second count margin (redundant total)
        (cell == 1) * tax, (cell == 2) * tax,       # weighted-sum cells
    ]).astype(float)
    # Targets reachable by a known bounded reweighting.
    g_true = np.clip(np.exp(rng.normal(0, 0.3, n)), 0.5, 2.0)
    totals = X.T @ (d * g_true)
    return X, d, totals


@pytest.mark.parametrize("method", ["linear", "raking", "logit"])
def test_calmar_hits_every_total_when_feasible(method):
    X, d, totals = _problem()
    res = calmar(X, d, totals, method=method, bounds=(0.2, 5.0), tol=1e-8)
    assert res.converged
    np.testing.assert_allclose(X.T @ res.weights, totals, rtol=1e-7)
    assert res.max_rel_residual < 1e-8


def test_logit_weights_stay_inside_bounds_and_are_reported():
    X, d, totals = _problem(seed=3)
    L, U = 0.6, 1.8
    res = calmar(X, d, totals, method="logit", bounds=(L, U), tol=1e-8, max_iter=200)
    g = res.weights / d
    assert g.min() >= L - 1e-9 and g.max() <= U + 1e-9
    assert res.bounds == (L, U)
    assert res.n_at_lower >= 0 and res.n_at_upper >= 0


def test_unreachable_total_is_reported_not_hidden():
    X, d, totals = _problem(seed=1)
    bad = totals.copy()
    bad[0] *= 6.0  # one count cell needs x6 but g <= 5 and the unit sum is shared
    res = calmar(X, d, bad, method="logit", bounds=(0.2, 5.0), tol=1e-8, max_iter=60)
    assert not res.converged
    assert res.max_rel_residual > 1e-3
    assert res.iterations < 60  # the stall rule stops it before the budget


def test_calmar_rejects_bad_bounds_and_shapes():
    X, d, totals = _problem()
    with pytest.raises(ValueError):
        calmar(X, d, totals, method="logit", bounds=(1.5, 2.0))
    with pytest.raises(ValueError):
        calmar(X, d[:-1], totals)


def test_feasible_interval_matches_hand_arithmetic():
    # Two units, d = [1, 1], values [10, 30], g in [0.5, 2], count target 2.
    # Min: g=(2, 0) is not allowed; start both at 0.5 (count 1), spend the
    # remaining 1 on the low unit first: g=(1.5, 0.5) -> 15 + 15 = 30.
    # Max: g=(0.5, 1.5) -> 5 + 45 = 50.
    lo, hi = feasible_interval(np.array([10.0, 30.0]), np.array([1.0, 1.0]), 2.0, (0.5, 2.0))
    assert lo == pytest.approx(30.0) and hi == pytest.approx(50.0)
    # A count outside the bound-reachable range is NaN.
    lo, hi = feasible_interval(np.array([10.0, 30.0]), np.array([1.0, 1.0]), 9.0, (0.5, 2.0))
    assert np.isnan(lo) and np.isnan(hi)


def test_lp_feasible_agrees_with_calmar():
    X, d, totals = _problem(seed=2)
    ok, g = lp_feasible(X, d, totals, (0.2, 5.0))
    assert ok and g is not None and g.min() >= 0.2 - 1e-9
    bad = totals.copy()
    bad[0] *= 6.0
    assert lp_feasible(X, d, bad, (0.2, 5.0))[0] is False
