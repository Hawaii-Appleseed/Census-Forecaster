"""Successive-difference replication (SDR) variance for ACS PUMS estimates.

The Census Bureau ships 80 replicate weights with each ACS PUMS release
(``PWGTP1..80`` on persons, ``WGTP1..80`` on households), constructed via
successive difference replication. The sampling variance of any weighted
estimate :math:`\\hat\\theta` is

.. math::

    \\widehat{\\mathrm{Var}}(\\hat\\theta)
        = \\frac{4}{R} \\sum_{r=1}^{R} (\\hat\\theta_r - \\hat\\theta_0)^2

with :math:`R = 80` for ACS, where :math:`\\hat\\theta_0` uses the
full-sample weight (``PWGTP`` / ``WGTP``) and :math:`\\hat\\theta_r` uses
replicate ``r``. :math:`SE = \\sqrt{\\widehat{\\mathrm{Var}}}`; the 90%
confidence interval is :math:`\\hat\\theta_0 \\pm 1.645\\,SE`.

The crucial efficiency property: **replicate weights change only the
aggregation, never the per-unit microdata.** Per-unit outcomes (tax
liability, poverty status, ...) are computed once; an estimate and its 80
replicate counterparts are then 81 weighted re-aggregations of the same
per-unit vector. This module provides those re-aggregations (weighted
totals and ratios over an ``(n_units, 1 + R)`` weight matrix whose column
0 is the full-sample weight) plus the SDR variance/SE/CI reduction. No
re-run of the tax engine is required.

**Scope.** SDR captures ACS *sampling* variance only. It does NOT capture
model uncertainty — imputation, take-up rates, CBO aging factors,
calibration targets. Any emitted interval must be labelled accordingly so
it is not over-interpreted as total uncertainty.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import ArrayLike

# ACS ships 80 replicate weights; the SDR scale factor is 4 / R.
ACS_REPLICATES: int = 80
ACS_SDR_FACTOR: float = 4.0 / ACS_REPLICATES
# 90% normal critical value — the Census standard for PUMS margins of error.
Z_90: float = 1.645


@dataclass(frozen=True)
class SDREstimate:
    """A point estimate with its SDR sampling standard error and CI."""

    point: float
    se: float
    ci_low: float
    ci_high: float
    z: float = Z_90

    @property
    def moe(self) -> float:
        """Half-width of the confidence interval (z * SE)."""
        return self.z * self.se

    @property
    def cv(self) -> float:
        """Coefficient of variation: SE as a fraction of |point|."""
        return self.se / abs(self.point) if self.point else float("nan")

    def as_dict(self, prefix: str) -> dict[str, float]:
        """Flatten to ``{prefix, prefix_se, prefix_ci90_low, prefix_ci90_high}``."""
        return {
            prefix: self.point,
            f"{prefix}_se": self.se,
            f"{prefix}_ci90_low": self.ci_low,
            f"{prefix}_ci90_high": self.ci_high,
        }


def _resolve_factor(factor: float | None, n_replicates: int) -> float:
    """SDR scale factor, defaulting to ``4 / R`` for the supplied replicate count."""
    if n_replicates <= 0:
        raise ValueError("need at least one replicate estimate")
    return 4.0 / n_replicates if factor is None else factor


def sdr_variance(
    point: float, replicate_estimates: ArrayLike, *, factor: float | None = None
) -> float:
    """SDR sampling variance of an estimate given its replicate estimates.

    ``factor`` defaults to ``4 / R`` (the ACS SDR convention) where ``R``
    is ``len(replicate_estimates)`` — so passing the full 80 ACS replicates
    yields the documented ``4 / 80``.
    """
    reps = np.asarray(replicate_estimates, dtype=float)
    if reps.ndim != 1:
        raise ValueError("replicate_estimates must be 1-D")
    f = _resolve_factor(factor, reps.size)
    return float(f * np.sum((reps - float(point)) ** 2))


def sdr_se(
    point: float, replicate_estimates: ArrayLike, *, factor: float | None = None
) -> float:
    """SDR sampling standard error (square root of :func:`sdr_variance`)."""
    return float(np.sqrt(sdr_variance(point, replicate_estimates, factor=factor)))


def summarize(
    point: float,
    replicate_estimates: ArrayLike,
    *,
    factor: float | None = None,
    z: float = Z_90,
) -> SDREstimate:
    """Bundle a point estimate + replicate estimates into an :class:`SDREstimate`."""
    se = sdr_se(point, replicate_estimates, factor=factor)
    p = float(point)
    return SDREstimate(point=p, se=se, ci_low=p - z * se, ci_high=p + z * se, z=z)


def _as_matrix(values: ArrayLike, weight_matrix: ArrayLike) -> tuple[np.ndarray, np.ndarray]:
    v = np.asarray(values, dtype=float)
    w = np.asarray(weight_matrix, dtype=float)
    if v.ndim != 1:
        raise ValueError("values must be 1-D (one per unit)")
    if w.ndim != 2:
        raise ValueError("weight_matrix must be 2-D (n_units, 1 + R)")
    if w.shape[1] < 2:
        raise ValueError(
            "weight_matrix needs a point column plus at least one replicate "
            f"column; got shape {w.shape}"
        )
    if v.shape[0] != w.shape[0]:
        raise ValueError(
            f"values length ({v.shape[0]}) does not match weight_matrix rows "
            f"({w.shape[0]})"
        )
    return v, w


def weighted_total_replicates(values: ArrayLike, weight_matrix: ArrayLike) -> np.ndarray:
    """Weighted totals under every weight column.

    Returns a 1-D array of length ``1 + R``; element 0 is the full-sample
    (point) total and elements ``1..R`` are the replicate totals.
    """
    v, w = _as_matrix(values, weight_matrix)
    return v @ w


def weighted_ratio_replicates(
    numerator: ArrayLike, denominator: ArrayLike, weight_matrix: ArrayLike
) -> np.ndarray:
    """Weighted ratios (numer-total / denom-total) under every weight column.

    Both numerator and denominator are re-aggregated with the *same* weight
    column, so the replicate ratios capture the numerator/denominator
    covariance automatically — the correct SDR treatment for a ratio
    estimator such as a poverty rate. Columns with a zero denominator total
    yield ``nan``.
    """
    v_n, w = _as_matrix(numerator, weight_matrix)
    v_d, _ = _as_matrix(denominator, weight_matrix)
    num = v_n @ w
    den = v_d @ w
    out = np.full(num.shape, np.nan, dtype=float)
    nz = den != 0
    out[nz] = num[nz] / den[nz]
    return out


def estimate_total(
    values: ArrayLike,
    weight_matrix: ArrayLike,
    *,
    factor: float | None = None,
    z: float = Z_90,
) -> SDREstimate:
    """SDR estimate of a weighted total over an ``(n_units, 1 + R)`` weight matrix."""
    est = weighted_total_replicates(values, weight_matrix)
    return summarize(est[0], est[1:], factor=factor, z=z)


def estimate_ratio(
    numerator: ArrayLike,
    denominator: ArrayLike,
    weight_matrix: ArrayLike,
    *,
    factor: float | None = None,
    z: float = Z_90,
) -> SDREstimate:
    """SDR estimate of a weighted ratio over an ``(n_units, 1 + R)`` weight matrix."""
    est = weighted_ratio_replicates(numerator, denominator, weight_matrix)
    return summarize(est[0], est[1:], factor=factor, z=z)


__all__ = [
    "ACS_REPLICATES",
    "ACS_SDR_FACTOR",
    "Z_90",
    "SDREstimate",
    "estimate_ratio",
    "estimate_total",
    "sdr_se",
    "sdr_variance",
    "summarize",
    "weighted_ratio_replicates",
    "weighted_total_replicates",
]
