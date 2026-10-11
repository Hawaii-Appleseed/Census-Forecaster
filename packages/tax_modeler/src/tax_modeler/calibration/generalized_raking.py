"""Generalized raking (calibration estimation) with bounded weight ratios.

Deville & Särndal (1992) calibration: find weights ``w`` close to the design
weights ``d`` (in a distance chosen by ``method``) such that every linear
constraint ``X.T @ w == totals`` holds. ``X`` is the ``n × k`` matrix of
calibration variables — cell indicators for count margins, ``value × indicator``
for weighted-sum margins (tax by bracket, AGI by bracket) — so count, status,
tax-total and AGI-total margins are solved **jointly**, one system, with
unit-level weight adjustments.

Why this exists
---------------
``ipf_orchestrator.calibrate_via_rake`` used to cycle three *per-bracket*
scaling steps (count rake, tax-total step, AGI-total step). A per-bracket
scalar moves every weight in the bracket by the same factor, so it cannot
change the bracket's weighted *mean* tax or AGI — but a count target ``N_b``
together with a tax target ``T_b`` fixes that mean at ``T_b / N_b``. The
sequential scheme therefore had no lever for the one quantity the joint
margins pin down, and it stopped at a compromise fixed point with counts
+12–19% over target in six brackets (October 2026 calibration report). A
joint calibration moves weight *within* the bracket (toward units with
higher or lower tax) and satisfies both, provided the target mean lies in the
range the bracket's units span (:func:`feasible_interval`).

Methods
-------
``"logit"``   Deville, Särndal & Sautory (1993) logit distance: the weight
              ratio ``g = w / d`` is confined to ``(L, U)``; bounded weights
              by construction, no post-hoc clipping.
``"raking"``  Exponential (classic raking) distance, ``g = exp(x'λ)``:
              positive but unbounded.
``"linear"``  GREG / chi-square distance, ``g = 1 + x'λ``: closed form, can
              go negative; used as the initial step and for diagnostics.

The multipliers ``λ`` are found by Newton's method on the calibration
equations; the Jacobian is ``X.T @ diag(d F'(Xλ)) @ X``, solved by
least-squares so redundant margins (e.g. count brackets and filing-status
cells both summing to the filer total) do not break the step.

References
----------
Deville, J.-C., & Särndal, C.-E. (1992). Calibration estimators in survey
sampling. *JASA* 87(418), 376–382.
Deville, J.-C., Särndal, C.-E., & Sautory, O. (1993). Generalized raking
procedures in survey sampling. *JASA* 88(423), 1013–1020.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

__all__ = [
    "CalmarResult",
    "calmar",
    "feasible_interval",
    "lp_feasible",
]


@dataclass
class CalmarResult:
    weights: np.ndarray
    g: np.ndarray                      # w / d
    lambdas: np.ndarray
    converged: bool
    iterations: int
    max_rel_residual: float
    residuals: np.ndarray              # achieved - target, per constraint
    achieved: np.ndarray
    n_at_lower: int = 0                # units with g within 1e-6 of L
    n_at_upper: int = 0
    method: str = "logit"
    bounds: Tuple[float, float] = (0.0, float("inf"))
    history: List[float] = field(default_factory=list)


def _logit_F(u: np.ndarray, L: float, U: float) -> Tuple[np.ndarray, np.ndarray]:
    """Logit distance ratio function and its derivative (DSS 1993)."""
    A = (U - L) / ((1.0 - L) * (U - 1.0))
    e = np.exp(np.clip(A * u, -700, 700))
    D = (U - 1.0) + (1.0 - L) * e
    F = (L * (U - 1.0) + U * (1.0 - L) * e) / D
    dF = A * (F - L) * (U - F) / (U - L)
    return F, dF


def _exp_F(u: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    e = np.exp(np.clip(u, -700, 700))
    return e, e


def _lin_F(u: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    return 1.0 + u, np.ones_like(u)


def calmar(
    X: np.ndarray,
    d: np.ndarray,
    totals: np.ndarray,
    *,
    method: str = "logit",
    bounds: Tuple[float, float] = (0.2, 5.0),
    tol: float = 1e-6,
    max_iter: int = 100,
    rel_scale: Optional[np.ndarray] = None,
    stall_tol: float = 1e-3,
) -> CalmarResult:
    """Solve ``X.T @ (d * F(X @ λ)) == totals`` for ``λ``; return the weights.

    Parameters
    ----------
    X:
        ``(n, k)`` calibration-variable matrix (float).
    d:
        ``(n,)`` starting weights (> 0).
    totals:
        ``(k,)`` target totals.
    method:
        ``"logit"`` (bounded, default), ``"raking"`` or ``"linear"``.
    bounds:
        ``(L, U)`` bounds on ``g = w / d`` for the logit method; ``L < 1 < U``.
    tol:
        Convergence: ``max_j |achieved_j - totals_j| / scale_j < tol`` where
        ``scale_j`` is ``|totals_j|`` (or 1 when the total is 0).
    rel_scale:
        Optional per-constraint scale replacing ``|totals|`` in the test and
        in the column normalization.
    stall_tol:
        Stop when three Newton steps improve the max relative residual by
        less than this fraction of itself (a bound is binding on a cell the
        system cannot reach); the result then reports ``converged=False``
        unless ``tol`` was met.
    """
    X = np.asarray(X, dtype=float)
    d = np.asarray(d, dtype=float)
    totals = np.asarray(totals, dtype=float)
    n, k = X.shape
    if d.shape != (n,) or totals.shape != (k,):
        raise ValueError("shape mismatch between X, d and totals")
    L, U = bounds
    if method == "logit" and not (L < 1.0 < U):
        raise ValueError(f"logit bounds must satisfy L < 1 < U, got {bounds}")

    scale = np.where(np.abs(totals) > 0, np.abs(totals), 1.0) if rel_scale is None \
        else np.asarray(rel_scale, dtype=float)
    # Column normalization: work with Xs = X / scale so every target is ~1.
    Xs = X / scale
    ts = totals / scale

    def F_of(u: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        if method == "logit":
            return _logit_F(u, L, U)
        if method == "raking":
            return _exp_F(u)
        if method == "linear":
            return _lin_F(u)
        raise ValueError(f"unknown method {method!r}")

    lam = np.zeros(k)
    history: List[float] = []
    converged = False
    it = 0
    g, dg = F_of(Xs @ lam)
    w = d * g
    resid = Xs.T @ w - ts
    max_rel = float(np.max(np.abs(resid))) if k else 0.0
    history.append(max_rel)
    for it in range(1, max_iter + 1):
        if max_rel < tol:
            converged = True
            it -= 1
            break
        J = Xs.T @ (Xs * (d * dg)[:, None])
        step, *_ = np.linalg.lstsq(J, -resid, rcond=None)
        # Damped Newton: halve the step while the residual does not fall.
        alpha = 1.0
        improved = False
        for _ in range(30):
            lam_try = lam + alpha * step
            g_t, dg_t = F_of(Xs @ lam_try)
            w_t = d * g_t
            if not np.all(np.isfinite(w_t)):
                alpha *= 0.5
                continue
            resid_t = Xs.T @ w_t - ts
            mr_t = float(np.max(np.abs(resid_t)))
            if mr_t < max_rel or alpha < 1e-6:
                lam, g, dg, w, resid, max_rel = lam_try, g_t, dg_t, w_t, resid_t, mr_t
                improved = True
                break
            alpha *= 0.5
        history.append(max_rel)
        if not improved:
            break
        if max_rel < tol:
            converged = True
            break
        # Stalled (bounds binding on an unreachable cell): stop rather than
        # spend the iteration budget on 1e-12 improvements.
        if len(history) >= 4 and history[-4] - max_rel < stall_tol * max(max_rel, 1e-12):
            break

    achieved = X.T @ w
    eps = 1e-6
    n_lo = int(np.sum(g <= L * (1 + eps))) if method == "logit" else int(np.sum(g <= 0))
    n_hi = int(np.sum(g >= U * (1 - eps))) if method == "logit" else 0
    return CalmarResult(
        weights=w, g=g, lambdas=lam, converged=converged, iterations=it,
        max_rel_residual=max_rel, residuals=achieved - totals, achieved=achieved,
        n_at_lower=n_lo, n_at_upper=n_hi, method=method,
        bounds=(L, U) if method == "logit" else (0.0, float("inf")),
        history=history,
    )


def feasible_interval(
    values: np.ndarray,
    d: np.ndarray,
    count_target: float,
    bounds: Tuple[float, float],
) -> Tuple[float, float]:
    """Range of ``Σ w·value`` reachable in one cell when ``Σ w = count_target``
    and ``w_i = d_i g_i`` with ``g_i ∈ [L, U]``.

    A fractional-knapsack bound: start every unit at ``L``, then raise units
    to ``U`` in decreasing (for the max) or increasing (for the min) order of
    ``value`` until the count budget is spent. Ignores every other margin, so
    it is a necessary condition for feasibility, not a sufficient one.
    """
    values = np.asarray(values, dtype=float)
    d = np.asarray(d, dtype=float)
    L, U = bounds
    base_count = float(np.sum(d) * L)
    budget = count_target - base_count
    if budget < -1e-9 or budget > float(np.sum(d) * (U - L)) + 1e-9:
        return (float("nan"), float("nan"))  # count itself is unreachable

    def _extreme(order: np.ndarray) -> float:
        total = float(np.sum(d * L * values))
        remaining = budget
        for i in order:
            room = d[i] * (U - L)
            take = min(room, remaining)
            total += take * values[i]
            remaining -= take
            if remaining <= 1e-12:
                break
        return total

    hi = _extreme(np.argsort(-values, kind="stable"))
    lo = _extreme(np.argsort(values, kind="stable"))
    return (lo, hi)


def lp_feasible(
    X: np.ndarray,
    d: np.ndarray,
    totals: np.ndarray,
    bounds: Tuple[float, float],
    *,
    rel_tol: float = 1e-6,
) -> Tuple[bool, Optional[np.ndarray]]:
    """Exact feasibility test: does any ``g ∈ [L, U]^n`` satisfy
    ``X.T @ (d g) == totals``? Uses ``scipy.optimize.linprog`` (HiGHS) with a
    zero objective. Returns ``(feasible, g)``.
    """
    from scipy.optimize import linprog

    X = np.asarray(X, dtype=float)
    d = np.asarray(d, dtype=float)
    totals = np.asarray(totals, dtype=float)
    scale = np.where(np.abs(totals) > 0, np.abs(totals), 1.0)
    A_eq = (X * d[:, None]).T / scale[:, None]
    b_eq = totals / scale
    L, U = bounds
    res = linprog(
        c=np.zeros(X.shape[0]), A_eq=A_eq, b_eq=b_eq,
        bounds=[(L, U)] * X.shape[0], method="highs",
        options={"primal_feasibility_tolerance": rel_tol},
    )
    if res.status == 0:
        return True, res.x
    return False, None
