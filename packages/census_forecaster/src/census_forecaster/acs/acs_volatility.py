"""ACS MOE-derived variance decomposition for per-cell phi calibration.

Decomposes year-over-year ACS variance into:
  var_sampling  — sampling noise derived from published ACS margins of error
  var_signal    — real economic variation (max(0, var_total - var_sampling))

Then maps noise_share = var_sampling / var_total to a per-cell damping
constant phi via an affine mapping bounded in [PHI_LO, PHI_HI].

Why published MOEs rather than PUMS replicate weights
------------------------------------------------------
Published ACS MOEs are computed by the Census Bureau from the same 80
replicate weights internally, so they are mathematically equivalent to
computing SE from scratch via PUMS replicates. Using them avoids fetching
~80x the data per state-year (16+ h naive wall-clock for 50 states x 15
years) while producing the same result.  AcsObservation already carries
``moe``, so the calibration panel needs no additional data sources.

SE conversion: SE = moe / Z_90, where Z_90 = 1.645 (Census publishes MOEs
at the 90% confidence level by convention).

SE in log space: SE_log ≈ SE_level / estimate  (delta method, valid when
SE_level / estimate << 1, which holds for the dollar-denominated indicators
this module targets).

Sampling variance for a log YoY rate (log A_t - log A_{t-1}):
  var_sampling_yoy ≈ SE_log(t)^2 + SE_log(t-1)^2

We average this across the panel years to get a representative
var_sampling for the (indicator, geoid) series.
"""
from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from common.models import AcsObservation

PHI_LO: float = 0.70
PHI_HI: float = 0.95
_Z90: float = 1.645  # Census 90% MOE convention


@dataclass(frozen=True)
class VarianceDecomposition:
    """Per-(indicator, geoid) panel variance decomposition.

    Produced by ``decompose_series`` and consumed by the calibration
    generator to build per-cell phi strata records.
    """
    indicator: str
    geoid: str
    year_start: int
    year_end: int
    var_total: float     # sample variance of log-YoY rates
    var_sampling: float  # mean of per-pair sampling variances
    var_signal: float    # max(0, var_total - var_sampling)
    noise_share: float   # var_sampling / var_total; 1.0 if var_total <= 0
    n_years: int         # number of YoY pairs


def decompose_series(observations: Sequence[AcsObservation]) -> VarianceDecomposition | None:
    """Decompose a sorted ACS time series into sampling + signal variance.

    Parameters
    ----------
    observations:
        ACS observations for one (indicator, geoid), sorted by year
        ascending.  Must have at least 2 entries with positive estimate
        and moe.

    Returns
    -------
    VarianceDecomposition or None if insufficient data.
    """
    obs = [o for o in observations if o.estimate > 0 and o.moe > 0]
    obs = sorted(obs, key=lambda o: o.year)
    if len(obs) < 2:
        return None

    indicator = obs[0].indicator
    geoid = obs[0].geoid

    se_log = [o.moe / (_Z90 * o.estimate) for o in obs]

    log_yoy: list[float] = []
    var_samp_pairs: list[float] = []
    for i in range(1, len(obs)):
        log_yoy.append(math.log(obs[i].estimate / obs[i - 1].estimate))
        # var(log A_t - log A_{t-1}) ≈ SE_log(t)^2 + SE_log(t-1)^2
        var_samp_pairs.append(se_log[i] ** 2 + se_log[i - 1] ** 2)

    var_total = _sample_variance(log_yoy)
    var_sampling = sum(var_samp_pairs) / len(var_samp_pairs)
    var_signal = max(0.0, var_total - var_sampling)
    noise_share = 1.0 if var_total <= 0 else min(1.0, var_sampling / var_total)

    return VarianceDecomposition(
        indicator=indicator,
        geoid=geoid,
        year_start=obs[0].year,
        year_end=obs[-1].year,
        var_total=var_total,
        var_sampling=var_sampling,
        var_signal=var_signal,
        noise_share=noise_share,
        n_years=len(log_yoy),
    )


def phi_from_noise_share(
    noise_share: float,
    phi_lo: float = PHI_LO,
    phi_hi: float = PHI_HI,
) -> float:
    """Map noise share to damping constant phi via affine interpolation.

    phi = phi_lo + (phi_hi - phi_lo) * noise_share

    phi_lo (0.70): high-signal series, trust recent momentum more.
    phi_hi (0.95): high-noise series, smooth heavily toward trend.

    Parameters
    ----------
    noise_share:
        Fraction of observed variance attributable to sampling noise.
        Clamped to [0, 1].

    Returns
    -------
    float in [phi_lo, phi_hi].
    """
    clamped = max(0.0, min(1.0, noise_share))
    return phi_lo + (phi_hi - phi_lo) * clamped


def cell_phi(
    decompositions: Sequence[VarianceDecomposition],
    n_min: int = 15,
) -> float | None:
    """Aggregate decompositions to a single cell phi.

    Uses the weighted median noise_share across counties (weighted by
    n_years). Returns None if total observations < n_min, so the caller
    falls through to the DEFAULT_PHI floor.

    Parameters
    ----------
    decompositions:
        All decompositions for one calibration cell (same indicator +
        pop_bucket + h_bucket).
    n_min:
        Minimum total YoY observations required. Default 15 (looser than
        the n=20 threshold for kappa/bias, since medians are more robust
        than coverage proportions).

    Returns
    -------
    float or None.
    """
    valid = [d for d in decompositions if d.n_years > 0]
    total_obs = sum(d.n_years for d in valid)
    if total_obs < n_min:
        return None

    noise_shares = [d.noise_share for d in valid]
    weights = [float(d.n_years) for d in valid]
    return phi_from_noise_share(_weighted_median(noise_shares, weights))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _sample_variance(values: Sequence[float]) -> float:
    vals = list(values)
    n = len(vals)
    if n < 2:
        return 0.0
    mean = sum(vals) / n
    return sum((v - mean) ** 2 for v in vals) / (n - 1)


def _weighted_median(values: Sequence[float], weights: Sequence[float]) -> float:
    pairs = sorted(
        [(v, w) for v, w in zip(values, weights, strict=False) if w > 0],
        key=lambda x: x[0],
    )
    if not pairs:
        return 0.0
    total = sum(w for _, w in pairs)
    cumulative = 0.0
    for v, w in pairs:
        cumulative += w
        if cumulative >= total / 2:
            return v
    return pairs[-1][0]
