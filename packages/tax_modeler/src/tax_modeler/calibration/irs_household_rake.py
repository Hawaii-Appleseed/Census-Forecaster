"""Fit the poverty population's tax-unit weights to IRS returns, household by household.

The poverty population (2024 one-year PUMS at the Census Bureau's weights) counts every
person who lives in Hawaiʻi. The IRS's Table 2 for Hawaiʻi
(:mod:`tax_modeler.calibration.irs_soi_hi_table2`) counts every federal return filed from a
Hawaiʻi address, the same universe; the Department of Taxation's tables count resident
returns only, a narrower group. This module rakes the tax-unit weight so the number of
returns in each of the seven IRS classes from $1 to $500,000 of federal AGI matches the IRS.

What moves and what does not. Only the tax-unit ``weight`` is scaled. ``hh_weight`` and every
household or person Census weight (WGTP, PWGTP) are left alone: the SPM poverty weights and
the household fifths use the Census household weight, which is controlled to population
totals, and fitting it to returns would break that control. Each household takes one factor
and every tax unit in it is scaled by that factor, so SPM units and households stay whole.

Method. Let ``C[h, k]`` be household h's weighted count of tax units with AGI in class k, the
same count ``tax_modeler.agreement.design`` makes for "Returns, IRS band ..." (``lo <= AGI <
hi``), and ``N[h, k]`` the number of such units (unweighted). The household factor is
``g_h = exp(N[h] . lambda)``: a household with two units in a class takes that class's ratio
twice, one spanning two classes takes both ratios, which is classic multiplicative raking on
unit counts. ``lambda`` solves ``sum_h g_h C[h] = targets`` by Newton's method with a
backtracking line search on the residual (the raking-ratio equations of Deville and Saerndal).
Households with no unit in a target class have ``N[h] = 0`` and keep a factor of exactly 1. The
fit is exact when it converges; otherwise ``CalibrationError``.

(A variant whose exponent is the household's *share* of its returns in each class, so that
every household in one class takes the same factor, fits just as exactly but is gentler and
less uneven; the October 30, 2026 trial used the count exponent, and so does this module.)

Pure numpy and pandas, no I/O.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tax_modeler.calibration import irs_soi_hi_table2 as irs
from tax_modeler.errors import CalibrationError, DataValidationError

#: The classes are fitted below this AGI; the $500,000+ classes are filled by the revenue
#: pipeline's synthetic top-income tail and are not fitted here.
TOP_AGI = 500_000


def irs_targets(year: int = irs.BASE_YEAR) -> dict[tuple[float, float], float]:
    """IRS returns in the seven classes from $1 to $500,000 of AGI, ``{(lo, hi): returns}``."""
    ret = irs.returns(year)
    out = {b: n for b, n in ret.items() if b[0] >= 1 and b[1] <= TOP_AGI}
    if len(out) != 7:
        raise DataValidationError(f"expected seven IRS classes from $1 to ${TOP_AGI:,}, found {len(out)}")
    return out


def _kish(w: np.ndarray) -> float:
    s2 = float((w * w).sum())
    return float(w.sum() ** 2 / s2) if s2 > 0 else 0.0


def household_factors(
    units: pd.DataFrame,
    weights,
    *,
    targets: dict[tuple[float, float], float],
    hh_col: str = "hh_id",
    agi_col: str = "federal_agi",
    max_iter: int = 100,
    tol: float = 1e-9,
) -> tuple[pd.Series, dict]:
    """Household factors that bring the weighted IRS-class return counts to ``targets``.

    ``weights`` is the tax-unit weight (array-like of length ``len(units)``) the counts are
    taken with; ``targets`` maps ``(lo, hi)`` to a return count. Returns ``(factors, info)``
    with ``factors`` a Series indexed by household id, one positive factor per household, and
    ``info`` the diagnostics: ``iterations``, ``max_abs_rel_error``, ``factor_min``,
    ``factor_max``, ``factor_p1``, ``factor_p99`` (over households, unweighted), and the Kish
    effective sample of the unit weights before and after (``ess_before``, ``ess_after``).
    """
    w = np.asarray(weights, dtype=float)
    if len(w) != len(units):
        raise DataValidationError("weights must have one value per unit")
    agi = units[agi_col].to_numpy(dtype=float)
    bands = list(targets)
    T = np.array([float(targets[b]) for b in bands])
    if (T <= 0).any():
        raise DataValidationError("every target must be positive")
    inside = np.column_stack([(agi >= lo) & (agi < hi) for lo, hi in bands])
    cu = w[:, None] * inside
    nu = inside.astype(float)

    ids = units[hh_col]
    keep = ids.notna().to_numpy()
    codes, uniques = pd.factorize(ids[keep])
    n_h = len(uniques)
    C = np.zeros((n_h, len(bands)))
    for k in range(len(bands)):
        C[:, k] = np.bincount(codes, weights=cu[keep, k], minlength=n_h)
    S = C.sum(axis=1)
    live = S > 0
    X = np.zeros_like(C)
    for k in range(len(bands)):                       # units of household h in class k
        X[:, k] = np.bincount(codes, weights=nu[keep, k], minlength=n_h)
    totals0 = C.sum(axis=0)
    if (totals0 <= 0).any():
        raise CalibrationError("a target class holds no weighted units, so it cannot be fitted")

    Cl, Xl = C[live], X[live]
    lam = np.zeros(len(bands))

    def rel_resid(lm: np.ndarray) -> np.ndarray:
        return (np.exp(Xl @ lm) @ Cl - T) / T

    iterations, r = 0, rel_resid(lam)
    err = float(np.abs(r).max())
    while err >= tol:
        if iterations >= max_iter:
            raise CalibrationError("IRS household rake did not converge", iterations=iterations, residual=err)
        iterations += 1
        g = np.exp(Xl @ lam)
        jac = (Cl.T * g) @ Xl / T[:, None]              # d(rel_resid)/d(lambda)
        step = np.linalg.solve(jac, r)
        t = 1.0
        while True:
            r_new = rel_resid(lam - t * step)
            if float(r_new @ r_new) <= (1 - 1e-4 * t) * float(r @ r) or t < 1e-12:
                break
            t /= 2
        lam, r = lam - t * step, r_new
        err = float(np.abs(r).max())

    factor = np.ones(n_h)
    factor[live] = np.exp(Xl @ lam)
    fs = pd.Series(factor, index=pd.Index(uniques, name=hh_col))
    per_unit = np.ones(len(units))
    per_unit[keep] = factor[codes]
    info = {
        "iterations": int(iterations),
        "max_abs_rel_error": err,
        "households": int(n_h),
        "households_targeted": int(live.sum()),
        "factor_min": float(factor.min()),
        "factor_max": float(factor.max()),
        "factor_p1": float(np.percentile(factor, 1)),
        "factor_p99": float(np.percentile(factor, 99)),
        "ess_before": _kish(w),
        "ess_after": _kish(w * per_unit),
    }
    return fs, info


def apply_household_factors(units: pd.DataFrame, factors: pd.Series, *, hh_col: str = "hh_id") -> pd.DataFrame:
    """A copy of ``units`` with ``weight`` scaled by its household's factor.

    Adds ``irs_rake_factor``. ``hh_weight`` and the replicate weight columns are not touched
    (see the module docstring). Units with no household id keep a factor of 1; a household
    id missing from ``factors`` is an error.
    """
    ids = units[hh_col]
    f = ids.map(factors)
    missing = f.isna() & ids.notna()
    if missing.any():
        raise DataValidationError(f"{int(missing.sum())} units belong to households with no factor")
    out = units.copy()
    f = f.fillna(1.0).to_numpy(dtype=float)
    out["irs_rake_factor"] = f
    out["weight"] = out["weight"].to_numpy(dtype=float) * f
    return out
