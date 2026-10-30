"""Methodology-agnostic poverty metrics.

These functions accept ``(resources, threshold, weights)`` and produce
the canonical poverty headcount, depth, and gap statistics. They serve
both OPM and SPM analyses — the difference is what you put into
``resources`` (pre-tax money income vs. post-tax-and-transfer SPM
resources) and ``threshold`` (OPM dollar thresholds vs. SPM
geographically-adjusted thresholds).

A "poverty unit" can be a household, family, SPMUnit, or tax unit
depending on the methodology. The functions don't care; they just count
units below the threshold weighted by the supplied population weights.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import numpy as np
import pandas as pd

from tax_modeler.errors import DataValidationError

ArrayLike = Union[np.ndarray, pd.Series, Sequence[float]]


def _to_arrays(
    resources: ArrayLike, threshold: ArrayLike, weights: ArrayLike
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r = np.asarray(resources, dtype=float).ravel()
    t = np.asarray(threshold, dtype=float).ravel()
    w = np.asarray(weights, dtype=float).ravel()
    if r.shape != w.shape:
        raise DataValidationError(
            f"resources and weights must have same length; got {r.shape} vs {w.shape}"
        )
    if t.shape != r.shape and t.size != 1:
        raise DataValidationError(
            f"threshold must be a scalar or have length {r.shape}; got {t.shape}"
        )
    if (w < 0).any():
        raise DataValidationError("weights must be non-negative")
    if (t <= 0).any():
        raise DataValidationError("threshold must be strictly positive")
    return r, t, w


def poverty_rate(
    resources: ArrayLike, threshold: ArrayLike, weights: ArrayLike
) -> float:
    """Weighted poverty headcount: share of people in units with resources < threshold.

    Range [0, 1]. Multiply by 100 for percentage points.
    """
    r, t, w = _to_arrays(resources, threshold, weights)
    below = r < t
    if w.sum() == 0:
        return 0.0
    return float(w[below].sum() / w.sum())


def _broadcast_threshold(t: np.ndarray, n: int) -> np.ndarray:
    """Return a per-row threshold array of length ``n``, broadcasting scalars."""
    if t.size == 1:
        return np.full(n, float(t.item()))
    return t


def poverty_depth(
    resources: ArrayLike, threshold: ArrayLike, weights: ArrayLike
) -> float:
    """Mean normalized poverty gap among the poor.

    For each poor unit, computes ``(threshold - resources) / threshold``,
    then takes the weighted mean over poor units only. Value of 0.0 means
    everyone poor is right at the threshold (boundary case); 1.0 means
    everyone poor has zero resources.

    Sometimes called "average poverty gap ratio" or P1/H in the
    Foster-Greer-Thorbecke family. Returns 0.0 if no one is poor.
    """
    r, t, w = _to_arrays(resources, threshold, weights)
    t = _broadcast_threshold(t, r.size)
    poor = r < t
    if not poor.any():
        return 0.0
    poor_w = w[poor]
    if poor_w.sum() == 0:
        return 0.0
    gap_norm = (t[poor] - r[poor]) / t[poor]
    return float((gap_norm * poor_w).sum() / poor_w.sum())


def poverty_gap(
    resources: ArrayLike, threshold: ArrayLike, weights: ArrayLike
) -> float:
    """Total dollar shortfall of the poor: weighted sum of ``max(threshold - resources, 0)``.

    Useful for fiscal-impact analysis: this is the total $ that would
    have to flow to the poor population to bring everyone up to the
    threshold. (Real-world programs are much less efficient than that
    counterfactual implies — but it sets an absolute lower bound.)
    """
    r, t, w = _to_arrays(resources, threshold, weights)
    t = _broadcast_threshold(t, r.size)
    shortfall = np.maximum(t - r, 0.0)
    return float((shortfall * w).sum())
