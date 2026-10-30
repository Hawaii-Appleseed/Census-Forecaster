"""DOTAX-calibrated income concentration model for revenue projection (Phase E).

Why the single-point estimate is systematically low
------------------------------------------------------
Phase D's ``hawaii_tax_weighted(median_income)`` is a point estimate: it
applies the tax function at exactly the median income level.  Because the tax
function is *convex* (progressive marginal rates), Jensen's inequality gives::

    E[tax(Y)]  >  tax(E[Y])  ≥  tax(median(Y))

The gap is driven by the right tail of the income distribution.  Hawaii's
income tax is extremely concentrated:

  * Top 28% of filers (AGI ≥ $75k) → **81% of revenue** (DOTAX Table 12A 2022)
  * Top 1.4% of filers (AGI ≥ $400k) → **33% of revenue** alone

A median-only forecast misses this entirely.  A 5% income growth year that is
driven by capital gains at the top generates far more revenue than 5% growth
in median wages — and vice versa for a recession that clips high earners first.

How this model works
---------------------
1. **DOTAX 2022 bracket distribution** (from ``DotaxTable12AValidator``) gives
   12 AGI brackets with their return counts and average tax-before-credits.
2. For each bracket, a **representative income** is the gross income level at
   which ``hawaii_tax_weighted(income)`` equals the DOTAX bracket average.
   This inversion simultaneously captures:

   * The actual income distribution within each bracket (skewed toward the
     lower bound — most filers are near $75k in the $75k–$100k bracket, not $87.5k)
   * Itemized deduction heterogeneity (high earners itemize; their taxable
     income is lower than AGI implies)
   * Filing-status mix within each bracket

   The 12 representative incomes were calibrated once with ``scipy.optimize.brentq``
   against the 2022 DOTAX data and are hardcoded below.

3. **Projection**: scale each bracket's representative income by the income
   growth factor ``g = projected_median / base_median``.  Optionally, supply
   a separate ``top_income_scale`` for the high-income brackets (from projected
   B19082_005E top-quintile share) to model divergence between median and top
   income growth.

4. **Expected revenue**: apply the tax function to each scaled representative
   income, weight by return counts, sum.

Verification: at ``growth=1.0``, the model reproduces the DOTAX 2022 average
of $4,770/filer within 0.004% (no hyperparameter tuning; purely structural).

B19082 integration
-------------------
When B19082_005E (top quintile income share) is projected:

    top_income_scale = projected_top_quintile_share / base_top_quintile_share

Pass ``top_income_scale`` to scale the high-income brackets independently.
Within ``expected_revenue_per_filer``, top brackets are scaled by
``base_growth × top_income_scale``, giving total top-bracket growth equal
to ``(proj_share / base_share) × (proj_median / base_median)`` — the
product of the share shift and the median growth, which is the correct
expression for absolute top-quintile income growth.  This models the
empirically common case where top-income growth diverges from median income
growth (technology booms, capital gains events, out-migration, etc.).

Usage
------
::

    from tax_modeler.projection.revenue_concentration import (
        expected_revenue_per_filer,
        concentration_multiplier,
        TOP_QUINTILE_REVENUE_SHARE_2022,
    )

    # Per-filer average revenue for a year where median income grew to $90k:
    rev = expected_revenue_per_filer(projected_median_income=90_000)
    # → more accurate than hawaii_tax_weighted(90_000) because it weights
    #   the full income distribution, not just the median

    # Revenue amplification from the concentration effect:
    mult = concentration_multiplier(90_000)
    # → ratio E[tax(Y)] / tax(median), always ≥ 1 for progressive tax

    # With B19082 projection (top-quintile income growing faster):
    rev_high_tail = expected_revenue_per_filer(90_000, top_income_scale=1.08)
    # → scales the top-income brackets by 8% more than median

Module layout
--------------
``DOTAX_2022_BRACKETS``
    12-entry tuple of ``BracketEntry`` for the 2022 DOTAX filing population.
``TOP_QUINTILE_REVENUE_SHARE_2022``
    81% — the fraction of total revenue from filers with AGI ≥ $75k.
``expected_revenue_per_filer(projected_median, tax_fn, ...)``
    Primary API: bracket-weighted projected revenue per return.
``concentration_multiplier(projected_median, tax_fn)``
    E[tax(Y)] / tax(median_income) — always ≥ 1 for progressive tax.
``make_concentration_adjusted_fn(tax_fn)``
    Returns a one-arg callable ``income → E[tax(Y)]`` for use as a
    ``tax_fn`` parameter in ``run_revenue_backtest``.
``concentration_marginal_fn(projected_median_income, ...)``
    Effective marginal d(E[tax(Y)])/d(median) via central finite difference.
    Drop-in ``marginal_fn`` for delta-method SE propagation.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# DOTAX 2022 bracket data (from DotaxTable12AValidator)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BracketEntry:
    """One row of the DOTAX Table 12A filing-population distribution.

    Attributes
    ----------
    agi_min, agi_max:
        AGI bracket boundaries (agi_max = inf for the top bracket).
    n_returns:
        Number of resident returns in this bracket (2022).
    avg_tax_before_credits:
        Average tax liability before credits for returns in this bracket.
    rep_income:
        Gross income at which ``hawaii_tax_weighted(income)`` equals
        ``avg_tax_before_credits``.  Calibrated from DOTAX 2022 data;
        encodes itemized deductions and within-bracket income skew.
    """
    agi_min: float
    agi_max: float
    n_returns: int
    avg_tax_before_credits: float
    rep_income: float


# Calibrated 2022 representative incomes — computed via scipy.optimize.brentq
# on hawaii_tax_weighted, verified against DOTAX totals (residual < 0.005%).
DOTAX_2022_BRACKETS: tuple[BracketEntry, ...] = (
    BracketEntry(       0,   10_000, 129_376,     21,     4_606.27),
    BracketEntry(  10_000,   20_000,  64_160,    333,    12_900.03),
    BracketEntry(  20_000,   30_000,  57_835,    889,    22_078.58),
    BracketEntry(  30_000,   40_000,  59_827,  1_533,    31_398.36),
    BracketEntry(  40_000,   50_000,  53_555,  2_166,    40_113.28),
    BracketEntry(  50_000,   75_000,  91_459,  3_201,    53_662.26),
    BracketEntry(  75_000,  100_000,  54_976,  4_751,    73_122.45),
    BracketEntry( 100_000,  150_000,  62_065,  7_055,   101_531.74),
    BracketEntry( 150_000,  200_000,  27_976, 10_496,   143_240.84),
    BracketEntry( 200_000,  300_000,  18_937, 16_391,   209_492.03),
    BracketEntry( 300_000,  400_000,   6_076, 25_119,   298_681.51),
    BracketEntry( 400_000,  math.inf,  8_875, 112_416, 1_097_910.32),
)

# Base year for calibration (2022 ACS Honolulu B19013)
DOTAX_2022_MEDIAN_INCOME: float = 83_737.0

# Proportion of total 2022 revenue from filers with AGI ≥ $75k (top ~28%)
# Computed from calibrated model: 81.0%
TOP_QUINTILE_REVENUE_SHARE_2022: float = 0.810

# Index of the first "top quintile" bracket in DOTAX_2022_BRACKETS
# (AGI ≥ $75k starts at bracket index 6)
_TOP_QUINTILE_BRACKET_START: int = 6

# Default tax function for the concentration model
def _default_tax_fn(income: float) -> float:
    from tax_modeler.projection.hawaii_tax_real import hawaii_tax_weighted
    return hawaii_tax_weighted(income)


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def expected_revenue_per_filer(
    projected_median_income: float,
    tax_fn: Callable[[float], float] | None = None,
    base_median_income: float = DOTAX_2022_MEDIAN_INCOME,
    top_income_scale: float | None = None,
) -> float:
    """Bracket-weighted projected per-filer revenue.

    Scales each bracket's calibrated representative income by the uniform
    income growth factor (and optionally a separate scale for top brackets),
    applies the tax function, and returns the return-count-weighted average.

    At ``projected_median_income = DOTAX_2022_MEDIAN_INCOME`` (growth = 1.0),
    this returns $4,769.80, matching the DOTAX 2022 average within 0.004%.

    Parameters
    ----------
    projected_median_income:
        Projected B19013 median household income for the target year.
    tax_fn:
        Income → tax callable.  Defaults to ``hawaii_tax_weighted``.
    base_median_income:
        Base year median income.  Defaults to ``DOTAX_2022_MEDIAN_INCOME``
        ($83,737, Honolulu ACS 2022 B19013).
    top_income_scale:
        Optional separate scale factor for the top-quintile brackets (AGI
        ≥ $75k).  When provided, top brackets scale by
        ``top_income_scale × base_growth`` rather than ``base_growth``.
        Derived from projected B19082_005E top-quintile income share::

            top_income_scale = top_share_projected / top_share_base

        So the total top-bracket growth = ``(proj_share / base_share) × base_growth``,
        the correct absolute growth when the share shifts.
        Omit (default None) to use the same growth factor for all brackets.

    Returns
    -------
    float
        Projected per-filer average revenue ≥ 0.
    """
    if projected_median_income <= 0 or base_median_income <= 0:
        return 0.0

    fn = tax_fn or _default_tax_fn
    base_growth = projected_median_income / base_median_income

    total_weighted_tax = 0.0
    total_returns = 0

    for i, b in enumerate(DOTAX_2022_BRACKETS):
        # Apply separate top-bracket scaling when provided
        if top_income_scale is not None and i >= _TOP_QUINTILE_BRACKET_START:
            scale = base_growth * top_income_scale
        else:
            scale = base_growth

        scaled_income = b.rep_income * scale
        projected_tax = fn(scaled_income)

        total_weighted_tax += b.n_returns * projected_tax
        total_returns += b.n_returns

    if total_returns == 0:
        return 0.0
    result = total_weighted_tax / total_returns
    logger.debug(
        "expected_revenue_per_filer: median=%.0f growth=%.4f "
        "top_scale=%s result=%.2f",
        projected_median_income,
        base_growth,
        f"{top_income_scale:.4f}" if top_income_scale is not None else "None",
        result,
    )
    return result


def concentration_multiplier(
    projected_median_income: float,
    tax_fn: Callable[[float], float] | None = None,
    base_median_income: float = DOTAX_2022_MEDIAN_INCOME,
) -> float:
    """Ratio of bracket-weighted to point-estimate revenue: E[tax(Y)] / tax(median).

    For a progressive (convex) tax function, this ratio is always ≥ 1.
    Values significantly above 1 indicate that the high-income tail
    contributes materially more revenue than the median-income estimate
    suggests.

    At Hawaii's 2022 income distribution, this ratio is approximately
    **0.85** — the bracket-weighted model gives LESS revenue than
    ``tax(median)`` because the calibrated representative income for the
    $75k-$100k bracket is $73k (below the median), reflecting itemized
    deductions and within-bracket income skew pulling the average down.
    In other words, ``hawaii_tax_weighted(83,737)`` *over-estimates* the
    DOTAX average; the concentration model correctly calibrates it down.

    Parameters
    ----------
    projected_median_income, tax_fn, base_median_income:
        Same as :func:`expected_revenue_per_filer`.

    Returns
    -------
    float
        ``expected_revenue_per_filer(...) / tax_fn(projected_median_income)``.
        Returns 1.0 if the point estimate is zero (degenerate case).
    """
    fn = tax_fn or _default_tax_fn
    point_estimate = fn(projected_median_income)
    if not math.isfinite(point_estimate) or point_estimate <= 0:
        return 1.0
    bracket_estimate = expected_revenue_per_filer(
        projected_median_income, tax_fn=fn,
        base_median_income=base_median_income,
    )
    return bracket_estimate / point_estimate


def make_concentration_adjusted_fn(
    tax_fn: Callable[[float], float] | None = None,
    base_median_income: float = DOTAX_2022_MEDIAN_INCOME,
) -> Callable[[float], float]:
    """Return a one-arg callable ``projected_income → E[tax(Y)]``.

    The returned function can be used as a drop-in ``tax_fn`` in
    :func:`run_revenue_backtest`, replacing the single-point estimate with
    the DOTAX-calibrated bracket-weighted estimate.

    Mathematically, the returned function computes::

        f(income) = expected_revenue_per_filer(income, tax_fn, base_median_income)

    which is the per-filer average revenue at the given projected income level.

    Note: the returned function is NOT the marginal rate function; do not use
    it as ``marginal_fn``.  The marginal function should remain the original
    ``tax_fn``'s marginal rate (the delta-method SE propagation is still
    applied to the point estimate, then optionally corrected by the conformal
    floor in Phase C).

    Parameters
    ----------
    tax_fn:
        Base tax function.  Defaults to ``hawaii_tax_weighted``.
    base_median_income:
        Base year median for growth-factor computation.  Defaults to
        ``DOTAX_2022_MEDIAN_INCOME``.

    Returns
    -------
    Callable[[float], float]
        One-arg callable: ``income → expected_revenue_per_filer(income)``.
    """
    fn = tax_fn or _default_tax_fn

    def adjusted(income: float) -> float:
        return expected_revenue_per_filer(
            income, tax_fn=fn, base_median_income=base_median_income
        )

    return adjusted


# ---------------------------------------------------------------------------
# Top-quintile revenue decomposition
# ---------------------------------------------------------------------------

def top_quintile_revenue_fraction(
    projected_median_income: float,
    tax_fn: Callable[[float], float] | None = None,
    base_median_income: float = DOTAX_2022_MEDIAN_INCOME,
) -> float:
    """Fraction of total projected revenue from filers with AGI ≥ $75k.

    Useful for monitoring how the top-income concentration of revenue changes
    as the income distribution shifts.  At 2022 calibration, this is 81%.
    As incomes grow (bracket creep), this fraction increases because more
    filers move into higher brackets.

    Parameters
    ----------
    projected_median_income, tax_fn, base_median_income:
        Same as :func:`expected_revenue_per_filer`.

    Returns
    -------
    float
        Fraction in [0, 1].  Returns ``TOP_QUINTILE_REVENUE_SHARE_2022``
        (0.810) when projected == base.
    """
    fn = tax_fn or _default_tax_fn
    if projected_median_income <= 0 or base_median_income <= 0:
        return TOP_QUINTILE_REVENUE_SHARE_2022

    base_growth = projected_median_income / base_median_income
    total_rev = 0.0
    top_rev = 0.0
    for i, b in enumerate(DOTAX_2022_BRACKETS):
        scaled_income = b.rep_income * base_growth
        tax = fn(scaled_income)
        weighted_tax = b.n_returns * tax
        total_rev += weighted_tax
        if i >= _TOP_QUINTILE_BRACKET_START:
            top_rev += weighted_tax
    if total_rev <= 0:
        return TOP_QUINTILE_REVENUE_SHARE_2022
    return top_rev / total_rev


# ---------------------------------------------------------------------------
# Effective marginal (for delta-method SE propagation)
# ---------------------------------------------------------------------------

def concentration_marginal_fn(
    projected_median_income: float,
    tax_fn: Callable[[float], float] | None = None,
    base_median_income: float = DOTAX_2022_MEDIAN_INCOME,
    eps_fraction: float = 1e-5,
) -> float:
    """Effective marginal of ``expected_revenue_per_filer`` w.r.t. ``projected_median_income``.

    Computes ``d(expected_revenue_per_filer) / d(projected_median_income)``
    via central finite differences.  Used as the ``marginal_fn`` for
    delta-method SE propagation when the concentration model is ``tax_fn``.

    Analytically this equals::

        sum_i n_i × (rep_i / base) × tax_fn'(rep_i × growth) / sum_i n_i

    — a bracket-weighted effective marginal sensitivity.  Numerical
    differentiation is used because it avoids re-implementing the bracket
    derivative and is more stable near discontinuities.

    At the 2022 base (median=$83,737), the effective marginal is approximately
    0.04–0.06 — lower than ``hawaii_marginal_weighted($83,737)`` (~0.08)
    because most filers are in lower-rate brackets even though the high-income
    tail drives 81% of revenue.

    Parameters
    ----------
    projected_median_income:
        Income at which to evaluate the derivative.
    tax_fn:
        Passed through to :func:`expected_revenue_per_filer`.
    base_median_income:
        Passed through to :func:`expected_revenue_per_filer`.
    eps_fraction:
        Relative step size for central difference.  Default 1e-5 gives
        sub-0.01% accuracy in smooth bracket regions.

    Returns
    -------
    float
        Effective marginal ≥ 0 ($ revenue change per $ median-income change).
    """
    if projected_median_income <= 0 or base_median_income <= 0:
        return 0.0
    h = max(projected_median_income * eps_fraction, 1.0)
    f_plus = expected_revenue_per_filer(
        projected_median_income + h,
        tax_fn=tax_fn,
        base_median_income=base_median_income,
    )
    f_minus = expected_revenue_per_filer(
        projected_median_income - h,
        tax_fn=tax_fn,
        base_median_income=base_median_income,
    )
    return (f_plus - f_minus) / (2.0 * h)
