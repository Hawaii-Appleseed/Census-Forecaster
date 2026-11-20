"""The lowest-earning adult's earnings on a tax-unit frame.

Two rules cap an expense at "the lower earner's earnings": the Hawaii care credit's earned income
limit (HRS §235-55.6(d)(1)) and the SPM's cap on work-related expenses (Interagency Technical Working
Group, *Observations*, March 2010: "The level of total work expenses subtracted from any family's
resources should be capped by the earning level of the lowest-earning adult"). Both need the same
number, so it lives here.

A tax unit carries each filer's wages and self-employment income as built from PUMS
(``primary_wagp`` / ``primary_semp`` and the ``secondary_`` pair), in base-year dollars, and an
``earned_income`` total aged to the tax year. The lesser spouse's *share* of the pair's base
earnings is applied to the aged total, so the aged total is kept.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def lower_earner_income(units: pd.DataFrame, *, earned_income_col: str = "earned_income",
                        filing_status_col: str = "filing_status") -> np.ndarray:
    """Earnings of the lowest-earning adult in each tax unit.

    Not married filing jointly: the filer's own earned income. Married filing jointly: the
    lesser spouse's earnings, which is zero when only one spouse earns. Needs the filer
    columns ``primary_wagp``, ``primary_semp``, ``secondary_wagp`` and ``secondary_semp``
    (absent ones count as zero, so a joint unit without them has no second earner).
    """
    def col(name):
        return units[name].fillna(0).to_numpy(dtype=float) if name in units.columns else np.zeros(len(units))

    earned = units[earned_income_col].fillna(0).to_numpy(dtype=float)
    p = np.maximum(col("primary_wagp") + col("primary_semp"), 0.0)
    s = np.maximum(col("secondary_wagp") + col("secondary_semp"), 0.0)
    joint = (units[filing_status_col].astype(str) == "married_filing_jointly").to_numpy()
    total = p + s
    with np.errstate(divide="ignore", invalid="ignore"):
        lesser_share = np.where(total > 0, np.minimum(p, s) / total, 0.0)
    return np.where(joint, earned * lesser_share, earned)
