"""NSLP (National School Lunch Program) — free / reduced-price school meals.

Census SPM Technical Documentation §4.3.1 specifies that the cash-equivalent
value of free and reduced-price school meals is counted as an SPM resource.
The value is computed as::

    annual_value = USDA per-meal reimbursement × school days × eligible_children

Eligibility:

  * Free meals:      household income ≤ 130% FPL (or categorical eligibility
                     via SNAP / TANF / Medicaid in CEP states; Hawaii has
                     a number of CEP schools)
  * Reduced price:   household income 131-185% FPL (≤ 185% FPL inclusive)

Hawaii operates on a separate USDA reimbursement schedule (higher than the
contiguous-US rate under 42 U.S.C. §1759a). For FY2022 the Hawaii free-meal
federal reimbursement rate was $4.21/meal; reduced-price was $3.81/meal.

The model here imputes per-tax-unit annual value from filing-unit income +
dependent count, with a flat 180-day school year (HI DOE standard).

Implementation notes:

  * Dependents are counted as "children" — slight overcount for college-age
    dependents (~5% over) but conservative for SPM-targeting accuracy.
  * No school-day participation rate adjustment — Census uses a "usually
    eats at school" CPS ASEC question; we assume eligible kids participate
    in school lunch (CPS take-up rate for free-eligible kids is ~95%).
  * The amount is added to SPM resources (gross-up positive), mirroring
    how SNAP / housing / LIHEAP are treated.

Reform DSL (``Reform.benefit_overrides["school_lunch"]``):

  * ``free_reimbursement_pct``         multiplier on per-meal free rate
  * ``reduced_reimbursement_pct``      multiplier on per-meal reduced rate
  * ``school_days``                    school-year length (default 180)
  * ``income_threshold_factor``        multiplier on the eligibility ratios
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ._fpl import fpl_year_for, hawaii_fpl

# USDA Food & Nutrition Service "National School Lunch Program Hawaii
# reimbursement rates" — annual notices in the Federal Register. Values
# below are the per-meal federal reimbursement (children eligible at
# severe-need / free / reduced-price tiers, blended; Hawaii rates differ
# from contiguous-US rates per 42 U.S.C. §1759a).
_HAWAII_FREE_RATE_PER_MEAL = {
    2022: 4.21, 2023: 4.51, 2024: 4.74, 2025: 4.87,
}
_HAWAII_REDUCED_RATE_PER_MEAL = {
    2022: 3.81, 2023: 4.11, 2024: 4.34, 2025: 4.47,
}
_SCHOOL_DAYS_PER_YEAR = 180  # HI DOE standard

_FREE_FPL_RATIO = 1.30
_REDUCED_FPL_RATIO = 1.85


@dataclass(frozen=True)
class SchoolLunchParameters:
    free_rate_per_meal: float = _HAWAII_FREE_RATE_PER_MEAL[2024]
    reduced_rate_per_meal: float = _HAWAII_REDUCED_RATE_PER_MEAL[2024]
    school_days: int = _SCHOOL_DAYS_PER_YEAR
    free_fpl_ratio: float = _FREE_FPL_RATIO
    reduced_fpl_ratio: float = _REDUCED_FPL_RATIO
    free_reimbursement_pct: float = 1.0
    reduced_reimbursement_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_school_lunch_parameters(tax_year: int = 2024) -> SchoolLunchParameters:
    """Return Hawaii NSLP parameters for the given tax year."""
    if tax_year not in _HAWAII_FREE_RATE_PER_MEAL:
        raise ConfigError(
            f"School-lunch parameters not defined for tax year {tax_year}",
            available=sorted(_HAWAII_FREE_RATE_PER_MEAL),
        )
    return SchoolLunchParameters(
        free_rate_per_meal=_HAWAII_FREE_RATE_PER_MEAL[tax_year],
        reduced_rate_per_meal=_HAWAII_REDUCED_RATE_PER_MEAL[tax_year],
    )


def with_school_lunch_overrides(
    base: SchoolLunchParameters, overrides: Mapping[str, object] | None
) -> SchoolLunchParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown school-lunch override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def compute_school_lunch_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: SchoolLunchParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "school_lunch_amount",
) -> pd.DataFrame:
    """Compute annual NSLP free/reduced school-lunch resource per tax unit.

    Eligibility is determined by household income / FPL ratio:
      * ≤ 130% FPL → free meals at the Hawaii free rate
      * 131-185% FPL → reduced-price at the Hawaii reduced rate
      * > 185% FPL → $0 (paid-meal rate is also reimbursable but minimal;
        Census SPM counts only free + reduced as positive resources)

    The output is added as a positive column read by
    :func:`tax_modeler.poverty.spm.compute_spm_resources` (via the
    ``school_lunch_col`` parameter).
    """
    p = with_school_lunch_overrides(
        params or hawaii_school_lunch_parameters(tax_year), overrides
    )
    df = units.copy()

    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    n_dep = df["num_dependents"].fillna(0).astype(int).clip(lower=0).to_numpy()
    hh_size = 1 + is_joint.astype(int) + n_dep
    income = df["income"].fillna(0).astype(float).to_numpy() if "income" in df.columns else (
        df["total_cash_income"].fillna(0).astype(float).to_numpy()
    )
    fpl = np.array([hawaii_fpl(fpl_year_for(tax_year), household_size=int(s)) for s in hh_size])
    fpl_ratio = np.where(fpl > 0, income / fpl, 0.0)

    free_cap = p.free_fpl_ratio * p.income_threshold_factor
    reduced_cap = p.reduced_fpl_ratio * p.income_threshold_factor

    annual_free = (
        p.free_rate_per_meal * p.free_reimbursement_pct * p.school_days
    )
    annual_reduced = (
        p.reduced_rate_per_meal * p.reduced_reimbursement_pct * p.school_days
    )

    has_kids = n_dep > 0
    per_child = np.where(
        fpl_ratio <= free_cap, annual_free,
        np.where(fpl_ratio <= reduced_cap, annual_reduced, 0.0),
    )
    amt = np.where(has_kids, per_child * n_dep, 0.0)
    df[out_col] = amt
    return df


__all__ = [
    "SchoolLunchParameters",
    "compute_school_lunch_for_units",
    "hawaii_school_lunch_parameters",
    "with_school_lunch_overrides",
]
