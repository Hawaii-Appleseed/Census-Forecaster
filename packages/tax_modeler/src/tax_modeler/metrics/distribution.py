"""Weighted distributional statistics: deciles, gini, palma, atkinson.

All functions accept ``values`` and ``weights`` arrays/Series of equal
length. Weights interpret each row as a population count: an unweighted
sample is the special case ``weights = ones_like(values)``.

The :func:`weighted_ntile_labels` helper generalizes the existing
``_weighted_quintile_labels`` in :mod:`tax_modeler.revenue.estimator`
to arbitrary ``n`` (deciles, percentiles, etc.) without code duplication.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from tax_modeler.errors import DataValidationError

ArrayLike = np.ndarray | pd.Series | Sequence[float]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_arrays(values: ArrayLike, weights: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(values, dtype=float).ravel()
    w = np.asarray(weights, dtype=float).ravel()
    if v.shape != w.shape:
        raise DataValidationError(
            f"values and weights must have the same length; got {v.shape} vs {w.shape}"
        )
    if (w < 0).any():
        raise DataValidationError("weights must be non-negative")
    if w.sum() == 0:
        raise DataValidationError("weights sum to zero; nothing to aggregate")
    return v, w


# ---------------------------------------------------------------------------
# Quantile labels
# ---------------------------------------------------------------------------


def weighted_ntile_labels(
    values: ArrayLike,
    weights: ArrayLike,
    n: int = 10,
    *,
    label_prefix: str = "D",
) -> pd.Series:
    """Assign weighted n-tile labels (e.g. ``"D1"`` … ``"D10"`` for deciles).

    Uses the weighted empirical CDF so each label represents an equal share
    of the population (not an equal number of rows).

    Parameters
    ----------
    values, weights:
        Equal-length, non-negative-weight arrays.
    n:
        Number of bins (5 = quintiles, 10 = deciles, 100 = percentiles).
    label_prefix:
        Prefix for the bin labels. Default ``"D"`` for "decile" (when ``n=10``).

    Returns
    -------
    pd.Series
        One label per input row, in the input's original order. The index
        of the input ``values`` is preserved when supplied as a Series;
        otherwise a default RangeIndex is used.
    """
    if n < 2:
        raise DataValidationError(f"n must be >= 2; got {n}")
    v, w = _to_arrays(values, weights)
    order = np.argsort(v, kind="mergesort")
    cum_w = np.cumsum(w[order])
    total_w = cum_w[-1]
    pct = cum_w / total_w
    edges = np.linspace(1.0 / n, 1.0, n)
    bins = np.clip(np.searchsorted(edges, pct) + 1, 1, n)

    labels = np.empty(len(v), dtype=object)
    labels[order] = [f"{label_prefix}{b}" for b in bins]

    if isinstance(values, pd.Series):
        return pd.Series(labels, index=values.index, dtype="object")
    return pd.Series(labels, dtype="object")


# ---------------------------------------------------------------------------
# Decile summary
# ---------------------------------------------------------------------------


def decile_summary(
    values: ArrayLike,
    weights: ArrayLike,
    *,
    n: int = 10,
    extra: dict | None = None,
) -> pd.DataFrame:
    """Per-decile (or n-tile) weighted means, totals, and shares.

    Parameters
    ----------
    values:
        The variable to bin and summarize (e.g. household income).
    weights:
        Population weights.
    n:
        Number of bins. Default 10 (deciles); pass ``5`` for quintiles.
    extra:
        Optional extra columns ``{name -> array_like_of_same_length}``.
        Each is reported as a weighted total per bin. Useful for
        reporting tax-paid or benefit-received per decile alongside
        income.

    Returns
    -------
    pd.DataFrame
        One row per bin, indexed by the bin label (``"D1"`` … ``"D{n}"``).
        Columns:

        * ``count_weighted`` — weighted count of rows in the bin
        * ``total`` — weighted sum of ``values`` in the bin
        * ``mean`` — total / count_weighted
        * ``share`` — total / grand total of ``values``
        * one column per ``extra`` key, weighted-summed
    """
    v, w = _to_arrays(values, weights)
    labels = weighted_ntile_labels(values, weights, n=n)
    df = pd.DataFrame({"value": v, "weight": w, "label": labels.values})

    if extra:
        for k, arr in extra.items():
            a = np.asarray(arr, dtype=float).ravel()
            if a.shape != v.shape:
                raise DataValidationError(
                    f"extra[{k!r}] has shape {a.shape}, expected {v.shape}"
                )
            df[k] = a

    grand_total = float((df["value"] * df["weight"]).sum())

    rows = []
    for b in [f"D{i}" for i in range(1, n + 1)]:
        sub = df[df["label"] == b]
        if sub.empty:
            continue
        cnt = float(sub["weight"].sum())
        tot = float((sub["value"] * sub["weight"]).sum())
        row = {
            "bin": b,
            "count_weighted": cnt,
            "total": tot,
            "mean": tot / cnt if cnt else 0.0,
            "share": tot / grand_total if grand_total else 0.0,
        }
        if extra:
            for k in extra:
                row[k] = float((sub[k] * sub["weight"]).sum())
        rows.append(row)

    out = pd.DataFrame(rows).set_index("bin")
    return out


# ---------------------------------------------------------------------------
# Inequality indices
# ---------------------------------------------------------------------------


def gini(values: ArrayLike, weights: ArrayLike) -> float:
    """Weighted Gini coefficient on a non-negative variable.

    Returns 0.0 for perfect equality, 1.0 for maximal inequality. Uses the
    standard ordered-cumulative formula.

    Negative values raise :class:`DataValidationError`. Top-coding clips
    are the caller's responsibility.
    """
    v, w = _to_arrays(values, weights)
    if (v < 0).any():
        raise DataValidationError(
            "gini requires non-negative values (clip to zero before calling "
            "if you need to handle losses)"
        )
    order = np.argsort(v, kind="mergesort")
    v = v[order]
    w = w[order]
    cw = np.cumsum(w)
    total_w = cw[-1]
    cum_v = np.cumsum(v * w)
    total_v = cum_v[-1]
    if total_v == 0:
        return 0.0
    # Lorenz integral via trapezoid rule on (cum_share_pop, cum_share_value)
    pop_share = cw / total_w
    val_share = cum_v / total_v
    pop_share = np.concatenate(([0.0], pop_share))
    val_share = np.concatenate(([0.0], val_share))
    # np.trapezoid is the post-NumPy-2.0 name; keep a fallback for 1.x
    trapezoid = getattr(np, "trapezoid", None) or np.trapz  # type: ignore[attr-defined]
    area_under_lorenz = trapezoid(val_share, pop_share)
    return float(1.0 - 2.0 * area_under_lorenz)


def palma(values: ArrayLike, weights: ArrayLike) -> float:
    """Palma ratio: top-decile share / bottom-40% share.

    Higher = more inequality. A typical OECD economy is ~1.0; the US is
    ~1.8; Brazil ~3.0. Returns ``inf`` if the bottom-40% share is zero.
    """
    v, w = _to_arrays(values, weights)
    if (v < 0).any():
        raise DataValidationError("palma requires non-negative values")
    deciles = weighted_ntile_labels(values, weights, n=10)
    df = pd.DataFrame({"v": v, "w": w, "d": deciles.values})
    df["vw"] = df["v"] * df["w"]
    bottom40 = df[df["d"].isin(["D1", "D2", "D3", "D4"])]["vw"].sum()
    top10 = df[df["d"] == "D10"]["vw"].sum()
    if bottom40 == 0:
        return float("inf")
    return float(top10 / bottom40)


def atkinson(values: ArrayLike, weights: ArrayLike, *, eps: float = 0.5) -> float:
    """Weighted Atkinson inequality index (epsilon-aversion parameter).

    ``eps`` controls inequality aversion: 0.0 = no aversion (always 0.0),
    0.5 = mild, 1.0 = substantial, 2.0 = strong. Standard publication
    practice is ``eps=0.5``.

    Range: 0.0 (perfect equality) to 1.0 (maximal inequality).
    """
    if eps < 0:
        raise DataValidationError(f"atkinson eps must be non-negative; got {eps}")
    v, w = _to_arrays(values, weights)
    if (v <= 0).any():
        # Atkinson is undefined for zero / negative values. Standard
        # treatment: shift by a tiny epsilon. Document the approach.
        v = np.clip(v, 1e-9, None)
    weighted_mean = (v * w).sum() / w.sum()
    if eps == 1.0:
        # Limit case: 1 - exp(weighted-mean of log(v) / weighted-mean of v)
        log_mean = np.exp((np.log(v) * w).sum() / w.sum())
        return float(1.0 - log_mean / weighted_mean)
    edm = ((v ** (1.0 - eps)) * w).sum() / w.sum()
    return float(1.0 - (edm ** (1.0 / (1.0 - eps))) / weighted_mean)
