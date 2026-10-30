"""Macro scenario income shocks for SB 3125 CD1 recession analysis.

Shocks are multiplicative adjustments applied AFTER project_tax_units_forward()
and apply_top_income_growth_premium(), and BEFORE behavioral response.

IMPORTANT: Shock values represent CUMULATIVE DEVIATIONS from the baseline
trajectory at each point in time — NOT year-on-year deltas. So a value of
−0.015 for 2028 means "income is 1.5% below the baseline trajectory in 2028,"
representing a depressed level after partial recovery from a deeper 2027 trough.

This matters because each year's `project_tax_units_forward()` call returns
income at the BASELINE trajectory for that year (independent of any prior
shocks). To model a recession with persistent effects, each year's shock must
encode the cumulative gap to baseline, not the year-on-year change.

Usage in forecast loop:
    projected = project_tax_units_forward(units, target_year=year)
    projected = apply_top_income_growth_premium(projected, ...)
    if macro_shock is not None:
        projected = apply_macro_recession_shock(projected, target_year=year,
                                                scenario=macro_shock)
    # ... rest of behavioral chain unchanged

NOTE: Update SB3125_CD1_FORECAST.md whenever these parameters change.
"""
from __future__ import annotations

import pandas as pd

# ---------------------------------------------------------------------------
# Moderate recession: trough 2027, gradual recovery through 2031
#
# Profile reflects empirical recession patterns (e.g., 2008-09): trough in
# year 1, partial recovery in years 2-3, full convergence by year 4-5.
# Hawaii-specific: tourism dependence makes recovery slower than mainland.
# ---------------------------------------------------------------------------

# All-filer cumulative deviation from baseline — applied to every income unit.
# Each value is the cumulative gap to the no-recession baseline at year-end.
_MODERATE_BASE_SHOCKS: dict[int, float] = {
    2027: -0.020,   # trough: 2.0% below baseline (recession year)
    2028: -0.015,   # 1.5% below baseline (partial recovery)
    2029: -0.010,   # 1.0% below baseline (continued recovery)
    2030: -0.005,   # 0.5% below baseline (nearly normalized)
    2031:  0.000,   # back to baseline trajectory (full recovery)
}

# Top-income extra cumulative deviation — additional gap beyond the base
# shock for filers above TOP_INCOME_RECESSION_THRESHOLD.
# Rationale: capital gains realizations and pass-through business income are
# highly cyclical. Empirical pattern (2008-09): top-1% income fell harder
# initially (−6 to −12% peak-to-trough) but recovered faster than median.
_MODERATE_TOP_EXTRA_SHOCKS: dict[int, float] = {
    2027: -0.015,   # +1.5% additional gap (total top-income: −3.5%)
    2028: -0.010,   # +1.0% additional gap (total: −2.5%)
    2029: -0.003,   # +0.3% additional gap (total: −1.3%)
    2030:  0.000,   # top-income fully recovered (total: −0.5%)
    2031:  0.000,   # both fully recovered
}

TOP_INCOME_RECESSION_THRESHOLD = 200_000  # income ≥ $200K receives extra shock

MACRO_SCENARIOS: dict[str, dict] = {
    "moderate": {
        "base_shocks":      _MODERATE_BASE_SHOCKS,
        "top_extra_shocks": _MODERATE_TOP_EXTRA_SHOCKS,
        "top_threshold":    TOP_INCOME_RECESSION_THRESHOLD,
        "description":      "Moderate recession onset 2027, partial rebound 2028",
    },
}


def apply_macro_recession_shock(
    df: pd.DataFrame,
    target_year: int,
    scenario: str = "moderate",
    income_col: str = "income",
) -> pd.DataFrame:
    """Apply a macro recession income shock to projected tax units.

    Applies a base shock to ALL filers (cumulative deviation from baseline)
    plus an additional top-income shock for filers above the income threshold
    (capturing capital-gains and business-income cyclicality).

    The shock values represent CUMULATIVE deviations from the baseline
    trajectory at each year — not year-on-year changes. This correctly models
    persistent recession effects: by year 2 income is still depressed (smaller
    gap to baseline), not "rebounded above baseline."

    Call AFTER project_tax_units_forward() and apply_top_income_growth_premium(),
    BEFORE apply_behavioral_response(). Returns a modified copy; does not mutate
    the input DataFrame.

    Args:
        df:          Projected tax units for the target year (income at baseline).
        target_year: The tax year being modeled (2027–2031).
        scenario:    Key into MACRO_SCENARIOS dict (currently only "moderate").
        income_col:  Income column to scale (default "income").

    Returns:
        Modified DataFrame with income scaled to the recession-adjusted level.
        For "moderate" recession in 2031 (full recovery), this is a no-op copy.

    Example:
        >>> # 2027 trough: all filers 2% below baseline; top filers 3.5% below
        >>> projected = apply_macro_recession_shock(projected, target_year=2027)
        # All filers: income × 0.980
        # Filers ≥ $200K: income × 0.980 × 0.985  (total: × 0.9653)
        >>> # 2028 partial recovery: all 1.5% below; top 2.5% below
        >>> projected = apply_macro_recession_shock(projected, target_year=2028)
        # All filers: income × 0.985
        # Filers ≥ $200K: income × 0.985 × 0.990  (total: × 0.9752)
    """
    params = MACRO_SCENARIOS[scenario]
    base_delta = params["base_shocks"].get(target_year, 0.0)
    top_delta  = params["top_extra_shocks"].get(target_year, 0.0)
    top_thresh = params["top_threshold"]

    out = df.copy()
    out[income_col] = out[income_col].astype(float)

    # Apply base shock to ALL filers
    if base_delta != 0.0:
        out[income_col] = out[income_col] * (1.0 + base_delta)

    # Apply extra shock to top-income filers
    if top_delta != 0.0:
        mask = out[income_col] >= top_thresh
        out.loc[mask, income_col] = out.loc[mask, income_col] * (1.0 + top_delta)

    return out
