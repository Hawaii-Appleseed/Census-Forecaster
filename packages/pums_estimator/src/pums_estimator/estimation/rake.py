"""Iterative Proportional Fitting (IPF) / raking for PUMS reweighting.

Given observed PUMS microdata weights and target marginal distributions
(county controls from census_forecaster), adjust the weights iteratively
so that the reweighted margins match the targets.

Algorithm
---------
Deming-Stephan iterative proportional fitting (1940).  Each full iteration
cycles through all margin variables.  For each variable, every record's
weight is scaled by::

    scale = target_total_for_category / current_weighted_total_for_category

Convergence criterion: the maximum absolute relative change in any margin
cell across one full iteration falls below ``tol`` (default 1e-4).

Reference
---------
Deming, W. E., & Stephan, F. F. (1940). On a Least Squares Adjustment of
a Sampled Frequency Table When the Expected Marginal Totals are Known.
*Annals of Mathematical Statistics*, 11(4), 427–444.
"""
from __future__ import annotations

from collections.abc import Mapping

import numpy as np

from ..models import PumsRecord, CountyControls


def rake(
    records: list[PumsRecord],
    controls: CountyControls,
    max_iter: int = 100,
    tol: float = 1e-4,
) -> list[float]:
    """Reweight PUMS records via IPF to match county margin controls.

    Parameters
    ----------
    records : list[PumsRecord]
        PUMS records for the source geography (PUMAs covering or
        overlapping the target county).  Typically pre-filtered and
        allocation-factor-adjusted before calling this function.
    controls : CountyControls
        Target marginal totals.  Only variables present in
        ``controls.margins`` are constrained; others float freely.
    max_iter : int
        Maximum number of full-cycle iterations (default 100).
    tol : float
        Convergence tolerance (default 1e-4).  Iteration stops when
        the maximum |scale − 1| across all margin cells is below ``tol``.

    Returns
    -------
    list[float]
        Reweighted record weights, in the same order as ``records``.
        Input ``PumsRecord`` objects are not mutated.

    Notes
    -----
    Records missing a variable referenced in ``controls.margins`` are
    skipped for that variable's raking step — their weight is unaffected
    by that constraint.
    """
    weights = [r.weight for r in records]
    if not records or not controls.margins:
        return weights

    for _iteration in range(max_iter):
        max_delta = 0.0

        for var_name, targets in controls.margins.items():
            # --- accumulate current weighted totals per category ---
            current: dict[str, float] = {cat: 0.0 for cat in targets}
            for rec, w in zip(records, weights):
                val = rec.variables.get(var_name)
                if val is None:
                    continue
                cat = _categorise(val, targets)
                if cat in current:
                    current[cat] += w

            # --- scale weights ---
            for i, (rec, w) in enumerate(zip(records, weights)):
                val = rec.variables.get(var_name)
                if val is None:
                    continue
                cat = _categorise(val, targets)
                if cat not in targets:
                    continue
                curr_total = current.get(cat, 0.0)
                target_total = targets[cat]
                if curr_total > 0 and target_total >= 0:
                    scale = target_total / curr_total
                    weights[i] = w * scale
                    delta = abs(scale - 1.0)
                    if delta > max_delta:
                        max_delta = delta

        if max_delta < tol:
            break

    return weights


def rake_arrays(
    weights,
    variables: Mapping[str, object],
    margins: Mapping[str, Mapping[str, float]],
    max_iter: int = 100,
    tol: float = 1e-4,
) -> np.ndarray:
    """:func:`rake` for records held as arrays, every record carrying every
    margin variable: record ``i`` has weight ``weights[i]`` and value
    ``variables[name][i]`` for each name in ``margins``.

    Returns the weights :func:`rake` returns, bit for bit. Each category's
    weighted total is the same running sum in record order (``np.bincount``
    adds its weights in input order), each record is scaled by the same
    quotient, and iteration stops at the same point. Only the category of
    each distinct value is looked up (:func:`_categorise`), once, instead of
    for every record on every pass.
    """
    w = np.array(weights, dtype=float)
    if len(w) == 0 or not margins:
        return w

    plan = []
    for var_name, targets in margins.items():
        values = np.asarray(variables[var_name], dtype=float)
        labels = list(targets)
        distinct, which = np.unique(values, return_inverse=True)
        cat_of = np.array([labels.index(c) if (c := _categorise(v, targets)) in targets else -1
                           for v in distinct.tolist()], dtype=np.int64)
        code = cat_of[which]
        member = code >= 0
        plan.append((member, code[member], np.array([targets[c] for c in labels], dtype=float)))

    for _iteration in range(max_iter):
        max_delta = 0.0
        for member, code, target in plan:
            current = np.bincount(code, weights=w[member], minlength=len(target))
            ok = (current > 0) & (target >= 0)
            scale = np.ones(len(target))
            scale[ok] = target[ok] / current[ok]
            scaled = ok[code]
            idx = np.flatnonzero(member)[scaled]
            w[idx] = w[idx] * scale[code[scaled]]
            if ok.any():
                max_delta = max(max_delta, float(np.abs(scale[ok] - 1.0).max()))
        if max_delta < tol:
            break
    return w


def _categorise(value: float, targets: dict[str, float]) -> str:
    """Map a numeric PUMS value to the matching category key in *targets*.

    Handles three label styles:
    - Exact integer string: "0", "1", "2", …
    - Ceiling / "X+" style: "2+", "3+", "4+" — the value ≥ threshold.
    - Range style: "2-4" — not yet implemented; falls back to str(int(value)).
    """
    int_val = int(value)
    str_val = str(int_val)
    if str_val in targets:
        return str_val
    for cat in targets:
        if cat.endswith("+"):
            try:
                threshold = int(cat[:-1])
            except ValueError:
                continue
            if int_val >= threshold:
                return cat
    return str_val
