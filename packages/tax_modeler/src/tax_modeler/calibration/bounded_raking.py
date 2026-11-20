"""Bounded generalized raking: one calibration that meets every linear target together.

Given base weights ``w0`` and constraints ``sum_i w0_i * r_i * x_ic = t_c`` (a count, a weighted dollar total,
a weighted AGI, ...), find weight ratios ``r_i = w_i / w0_i`` inside ``[lower, upper]`` that meet all of them at
once. This is the calibration estimator of Deville and Särndal (1992) with their bounded ("logit") distance:

    r_i = ( L (U - 1) + U (1 - L) e^{A u_i} ) / ( (U - 1) + (1 - L) e^{A u_i} ),   u_i = x_i . lambda,
    A = (U - L) / ((1 - L)(U - 1)),

so ``r_i = 1`` at ``u = 0`` and ``r_i`` runs from ``L`` to ``U`` as ``u`` runs from minus to plus infinity: every
solution is inside the bounds by construction, and ``lambda`` has one entry per constraint (tens, not tens of
thousands). The system ``f_c(lambda) = sum_i a_ic r_i(lambda) / t_c - 1 = 0`` is solved by Levenberg-Marquardt
on the relative residuals, which is Newton's method with damping.

**When the targets cannot all be met inside the bounds** the same least-squares step returns the ratios that
minimise the sum of squared relative misses (``sum_c w_c (miss_c / t_c)^2``) among all ratios of this form. That is
the documented soft fallback: it is never silent. :class:`CalibrationResult` carries ``exact`` (whether every
target is met to ``tol``), every constraint's residual, and for each constraint the range its value can take
with only the bounds in force (``achievable_low`` / ``achievable_high``), so a target outside its own range
(infeasible alone) is told apart from targets that are each reachable but not together.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import least_squares


@dataclass
class CalibrationResult:
    """The solved ratios and a full account of how every target came out."""

    ratios: np.ndarray                  # w / w0, one per unit
    exact: bool                         # every target met to ``tol``
    iterations: int
    table: pd.DataFrame                 # one row per constraint: target, value, residual, achievable range
    bounds: tuple[float, float]
    tol: float
    message: str = ""
    dropped: list[str] = field(default_factory=list)   # constraints with no units, left out
    screened: list[str] = field(default_factory=list)  # constraints no weights inside the bounds can meet, left out

    @property
    def max_abs_relative_miss(self) -> float:
        return float(self.table["relative_miss"].abs().max())

    def conflicts(self) -> pd.DataFrame:
        """The constraints missed by more than ``tol``, worst first."""
        t = self.table[self.table["relative_miss"].abs() > self.tol]
        return t.reindex(t["relative_miss"].abs().sort_values(ascending=False).index)

    def effective_sample_size(self, w0) -> float:
        w = np.asarray(w0, dtype=float) * self.ratios
        return float(w.sum() ** 2 / (w ** 2).sum())

    def ratio_summary(self) -> dict[str, float]:
        r = self.ratios
        q = np.percentile(r, [1, 5, 25, 50, 75, 95, 99])
        lo, hi = self.bounds
        return {"min": float(r.min()), "p1": float(q[0]), "p5": float(q[1]), "p25": float(q[2]),
                "median": float(q[3]), "p75": float(q[4]), "p95": float(q[5]), "p99": float(q[6]),
                "max": float(r.max()), "share_at_lower": float(np.mean(r <= lo * (1 + 1e-6))),
                "share_at_upper": float(np.mean(r >= hi * (1 - 1e-6)))}


def _ratio_fn(lower: float, upper: float):
    """The bounded raking map ``u -> r`` and its derivative (numerically stable form)."""
    A = (upper - lower) / ((1.0 - lower) * (upper - 1.0))

    def r_of(u):
        # r = (L(U-1) + U(1-L) e) / ((U-1) + (1-L) e), e = exp(A u); written with a logistic to avoid overflow
        z = np.clip(A * u, -700.0, 700.0)
        s = 1.0 / (1.0 + np.exp(-z))                      # logistic(A u)
        # e/(c + k e) with c = U-1, k = 1-L :  = s / (c (1 - s) + k s)   [divide top and bottom by (1 + e)]
        c, k = upper - 1.0, 1.0 - lower
        denom = c * (1.0 - s) + k * s
        num = lower * c * (1.0 - s) + upper * k * s
        return num / denom

    def dr_du(u):
        z = np.clip(A * u, -700.0, 700.0)
        s = 1.0 / (1.0 + np.exp(-z))
        c, k = upper - 1.0, 1.0 - lower
        denom = c * (1.0 - s) + k * s
        num = lower * c * (1.0 - s) + upper * k * s
        # d/ds (num/denom) * ds/du, ds/du = A s (1 - s)
        dnum = -lower * c + upper * k
        ddenom = -c + k
        return (dnum * denom - num * ddenom) / denom ** 2 * A * s * (1.0 - s)

    return r_of, dr_du


def calibrate(w0, X, targets, *, names=None, bounds=(0.2, 5.0), tol: float = 1e-6, max_nfev: int = 400,
              importance=None, screen: bool = True, ridge: float = 0.0) -> CalibrationResult:
    """Solve ``sum_i w0_i r_i X[i, c] = targets[c]`` for every constraint ``c`` with ``r`` inside ``bounds``.

    ``X``: (n units, m constraints), the value each unit contributes to each constraint per unit of weight (an
    indicator for a count, ``tax`` times an indicator for a tax total, ...). ``targets``: length m. ``tol``:
    the relative miss under which a target counts as met. ``importance``: optional per-constraint weights on the
    squared relative misses in the soft case (all 1 by default). Constraints with no unit that contributes
    (``X[:, c]`` all zero) are dropped and listed in ``dropped``. ``screen``: a constraint whose target lies
    outside the range its own units can reach inside the bounds (``alone_feasible`` False) cannot be met by any
    weights; chasing it would only pull the others off their targets, so it is left out of the solve, listed in
    ``screened`` and reported at the value the other constraints leave it. ``ridge``: when positive, the squared
    relative misses are minimised together with ``ridge * |lambda|^2`` (ridge calibration), which keeps the ratios
    near 1 unless the targets need them elsewhere; ``0`` is the exact solve, whose limit this is as ``ridge`` falls.
    Use it when the exact solve cannot meet every target: it never reports a miss as a hit.
    """
    lower, upper = float(bounds[0]), float(bounds[1])
    if not (0 < lower < 1 < upper):
        raise ValueError(f"bounds must satisfy 0 < lower < 1 < upper, got {bounds}")
    w0 = np.asarray(w0, dtype=float)
    X = np.asarray(X, dtype=float)
    t = np.asarray(targets, dtype=float)
    n, m = X.shape
    names = list(names) if names is not None else [f"c{j}" for j in range(m)]
    imp = np.ones(m) if importance is None else np.asarray(importance, dtype=float)

    keep = np.abs(X).sum(axis=0) > 0
    dropped = [names[j] for j in range(m) if not keep[j]]
    X, t, imp = X[:, keep], t[keep], imp[keep]
    kept_names = [names[j] for j in range(m) if keep[j]]
    if (np.abs(t) < 1e-12).any():
        raise ValueError("a target of zero has no relative miss; drop it or give it a floor")
    contrib_all = w0[:, None] * X
    low_all = np.where(contrib_all > 0, contrib_all * lower, contrib_all * upper).sum(axis=0)
    high_all = np.where(contrib_all > 0, contrib_all * upper, contrib_all * lower).sum(axis=0)
    slack = 1e-9 * np.abs(t)
    alone = (t >= low_all - slack) & (t <= high_all + slack)
    solve_cols = alone if screen else np.ones(len(t), dtype=bool)
    screened = [kept_names[j] for j in range(len(t)) if not solve_cols[j]]
    full_X, full_t = X, t
    X, t, imp = X[:, solve_cols], t[solve_cols], imp[solve_cols]

    A = w0[:, None] * X / t[None, :]                 # relative contribution: sum_i A_ic r_i = 1 when met
    scale = np.sqrt((X ** 2 * w0[:, None]).sum(axis=0) / w0.sum())
    scale[scale == 0] = 1.0
    Z = X / scale[None, :]                            # u_i = Z_i . lambda, columns on a common scale
    r_of, dr_du = _ratio_fn(lower, upper)
    sw = np.sqrt(imp)

    sq = np.sqrt(ridge)

    def resid(lam):
        r = r_of(Z @ lam)
        f = sw * (A.T @ r - 1.0)
        return np.concatenate([f, sq * lam]) if ridge > 0 else f

    def jac(lam):
        d = dr_du(Z @ lam)
        J = sw[:, None] * (A.T @ (d[:, None] * Z))
        return np.vstack([J, sq * np.eye(len(lam))]) if ridge > 0 else J

    lam0 = np.zeros(Z.shape[1])
    sol = least_squares(resid, lam0, jac=jac, method="lm" if ridge <= 0 else "trf",
                        xtol=1e-14, ftol=1e-14, gtol=1e-14, max_nfev=max_nfev)
    r = r_of(Z @ sol.x)
    value = ((w0[:, None] * full_X) * r[:, None]).sum(axis=0)       # every constraint's value, solved or not
    table = pd.DataFrame({"constraint": kept_names, "target": full_t, "value": value,
                          "relative_miss": value / full_t - 1.0, "achievable_low": low_all,
                          "achievable_high": high_all, "alone_feasible": alone, "in_solve": solve_cols})
    solved_miss = np.abs(table.loc[table["in_solve"], "relative_miss"]).max() if solve_cols.any() else 0.0
    exact = bool(solved_miss <= tol and not screened)
    return CalibrationResult(ratios=r, exact=exact, iterations=int(sol.nfev), table=table,
                             bounds=(lower, upper), tol=tol, message=str(sol.message), dropped=dropped,
                             screened=screened)
