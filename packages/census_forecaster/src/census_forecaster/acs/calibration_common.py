"""Shared calibration primitives used by both ACS and BLS calibration.

Both calibration generators (ACS dollar-space and BLS log-space) need:

1. A bisection algorithm that finds a multiplicative SE-rescale factor
   bringing empirical CI coverage into a target band [low, high].
2. A geometric bias clamping rule (clip per-cell `mean(log(point/actual))`
   to ±log(1.10) and gate at `n >= n_threshold`).

The geometry of CI rescaling differs between the two calibration paths
— ACS rescales `(point - ci90_low)` directly (dollar-space symmetric),
BLS rescales `forecast_se_log` then exponentiates (log-space symmetric)
— so the *coverage function* itself stays in each module. What we share
here is the bisection algorithm parameterized over a `coverage_fn:
Callable[[float], float]`.

Putting this in `acs/calibration_common.py` keeps the import graph
acyclic: BLS imports it from here; ACS imports it from here; neither
imports the other. The placement under `acs/` is historical (the ACS
v3 layer was first); a future refactor could lift it to a top-level
`calibration_common.py` if more modules need it.
"""
from __future__ import annotations

import math
from collections.abc import Callable

# Default coverage band for the per-cell bisection. Both ACS and BLS
# target the same [85%, 95%] band — wider than ±2.5pp would admit too
# much per-cell drift; tighter would over-tune to the calibration sample.
DEFAULT_COVERAGE_LOW: float = 0.85
DEFAULT_COVERAGE_HIGH: float = 0.95


def bisect_for_target_coverage(
    coverage_fn: Callable[[float], float],
    target_low: float = DEFAULT_COVERAGE_LOW,
    target_high: float = DEFAULT_COVERAGE_HIGH,
    max_iter: int = 30,
    tol: float = 5e-3,
    widen_upper: float = 5.0,
    narrow_lower: float = 0.1,
) -> tuple[float, float]:
    """Bisect on the SE multiplicative factor to land coverage in band.

    `coverage_fn` is a pure function from factor (float) to empirical
    coverage (float in [0, 1] or NaN if no folds). The function must
    be monotonic-increasing in `factor` — larger factor widens CIs
    and increases coverage. Both ACS and BLS coverage functions satisfy
    this by construction.

    Returns `(factor, achieved_coverage)`. Behavior:

    * If coverage at factor=1.0 is already in `[target_low, target_high]`,
      returns `(1.0, cov0)` unchanged.
    * If undercover, searches `[1.0, widen_upper]` (default 5×).
    * If overcover, searches `[narrow_lower, 1.0]` (default 0.1×).
    * If the search bounds don't bracket the band, returns the closer
      endpoint with its achieved coverage — caller can decide whether to
      accept.

    The 5× widening cap and 10× narrowing floor are conservative: they
    cover the worst-case empirical κ values seen in our panels (~3-4×)
    without admitting pathological factors that would clearly indicate
    a degenerate cell (n too low, or the coverage curve is non-monotonic
    due to discretisation in a small-n cell).
    """
    cov0 = coverage_fn(1.0)
    if not math.isfinite(cov0):
        return (1.0, cov0)
    if target_low <= cov0 <= target_high:
        return (1.0, cov0)

    if cov0 < target_low:
        lo, hi = 1.0, widen_upper
    else:
        lo, hi = narrow_lower, 1.0

    cov_lo = coverage_fn(lo)
    cov_hi = coverage_fn(hi)
    if cov_lo > target_high:
        return (lo, cov_lo)
    if cov_hi < target_low:
        return (hi, cov_hi)

    for _ in range(max_iter):
        mid = (lo + hi) / 2.0
        cov = coverage_fn(mid)
        if target_low <= cov <= target_high:
            return (round(mid, 4), cov)
        if cov < target_low:
            lo = mid
        else:
            hi = mid
        if hi - lo < tol:
            break
    final = (lo + hi) / 2.0
    return (round(final, 4), coverage_fn(final))


__all__ = [
    "DEFAULT_COVERAGE_LOW",
    "DEFAULT_COVERAGE_HIGH",
    "bisect_for_target_coverage",
]
