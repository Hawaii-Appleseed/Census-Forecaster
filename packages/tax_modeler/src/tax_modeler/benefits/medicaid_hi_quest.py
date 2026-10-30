"""Hawaii Medicaid (Med-QUEST).

Hawaii adopted Medicaid expansion (138% FPL for non-elderly adults) under
the ACA in 2014 via the Med-QUEST waiver. Categorical pathways apply
above 138% FPL for children (up to 313% FPL via Hawaii's CHIP-equivalent),
pregnant women (up to 196% FPL), and elderly/disabled (varies, generally
100% FPL with asset tests).

Benefit value per enrollee is the actuarial value of services received
— not a cash transfer. We use the FY2024 HI Med-QUEST per-member-per-month
(PMPM) amount as the imputed in-kind benefit:

  * Adult expansion: ~$650 PMPM ($7,800/year)
  * Children: ~$320 PMPM ($3,840/year)
  * Aged/blind/disabled: ~$1,950 PMPM ($23,400/year, includes long-term care)

Phase 5 simplifications:

  * Categorical pathways are approximated by household composition (kids
    raise the income limit). Pregnancy and disability pathways aren't
    flagged on ACS PUMS reliably; Phase 4-style take-up imputation
    against DHS Med-QUEST rolls handles the gap.
  * Asset tests for aged/disabled pathways aren't modeled.
  * Long-term care (NH waiver) is bundled into the aged/disabled PMPM.
  * Children's CHIP-equivalent (138-313% FPL) is treated as Medicaid for
    fiscal purposes — they're funded jointly under the same state line
    item.

Reform DSL (``Reform.benefit_overrides["medicaid"]``):

  * ``adult_pmpm_pct``           multiplier on adult per-member-per-month value
  * ``income_threshold_factor``   multiplier on FPL caps (default 1.0)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ._fpl import fpl_year_for, hawaii_fpl

# 2024 HI Med-QUEST PMPM (annualized × 12). State Plan Amendment + CMS-64
# expenditures are the production source.
_PMPM_ADULT_2024 = 650.0 * 12          # $7,800/year
_PMPM_CHILD_2024 = 320.0 * 12          # $3,840/year
_PMPM_AGED_DISABLED_2024 = 1_950.0 * 12   # $23,400/year (includes LTC)


@dataclass(frozen=True)
class MedicaidParameters:
    adult_expansion_fpl_cap: float = 1.38     # 138% FPL
    child_fpl_cap: float = 3.13                # 313% FPL (HI CHIP-equivalent)
    pregnant_fpl_cap: float = 1.96             # 196% FPL (categorical, not modeled here)
    aged_fpl_cap: float = 1.00                 # 100% FPL (categorical, with asset tests)
    aged_threshold_age: int = 65
    pmpm_adult_annual: float = _PMPM_ADULT_2024
    pmpm_child_annual: float = _PMPM_CHILD_2024
    pmpm_aged_disabled_annual: float = _PMPM_AGED_DISABLED_2024
    adult_pmpm_pct: float = 1.0
    child_pmpm_pct: float = 1.0
    aged_pmpm_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_medicaid_parameters() -> MedicaidParameters:
    return MedicaidParameters()


def with_medicaid_overrides(
    base: MedicaidParameters, overrides: Mapping[str, object] | None
) -> MedicaidParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown Medicaid override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def compute_medicaid_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: MedicaidParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "medicaid_amount",
    receives_col: str = "medicaid_receives",
    family_income_col: str | None = None,
    family_size_col: str | None = None,
) -> pd.DataFrame:
    """Compute Hawaii Med-QUEST in-kind benefit + receives flag.

    Adds two columns to a copy of ``units``:

    * ``out_col`` — annual imputed dollar value of Medicaid services
    * ``receives_col`` — boolean: True where any household member is
      categorically eligible

    The dollar value is summed over all eligible household members:
    ``adult_pmpm × n_adults_eligible + child_pmpm × n_children_eligible
    + aged_pmpm × n_aged_eligible``.

    ``tax_year`` selects the FPL table used for the income tests (default
    2024). Pass the run's tax year on forward-projected runs so the
    eligibility thresholds match the aged income basis.
    """
    p = with_medicaid_overrides(params or hawaii_medicaid_parameters(), overrides)
    df = units.copy()

    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
    hh_size = 1 + is_joint.astype(int) + n_dep
    income = df["income"].fillna(0).astype(float).to_numpy()

    # Income/size basis for the FPL test. Use family-grain columns (income and
    # persons summed across tax units in a household / SPM unit) when supplied;
    # otherwise the tax-unit grain. Per-tax-unit testing splits a household's
    # income across small units and overstates eligibility.
    if (
        family_income_col is not None and family_size_col is not None
        and family_income_col in df.columns and family_size_col in df.columns
    ):
        test_income = df[family_income_col].fillna(0).astype(float).to_numpy()
        test_size = df[family_size_col].fillna(0).clip(lower=1).astype(int).to_numpy()
    else:
        test_income = income
        test_size = hh_size
    fpl = np.array([hawaii_fpl(fpl_year_for(tax_year), household_size=int(s)) for s in test_size])
    fpl_ratio = np.where(fpl > 0, test_income / fpl, 0.0)

    # Categorical eligibility checks
    adult_cap = p.adult_expansion_fpl_cap * p.income_threshold_factor
    child_cap = p.child_fpl_cap * p.income_threshold_factor
    aged_cap = p.aged_fpl_cap * p.income_threshold_factor

    # Adult expansion: count adults (1 + spouse) where below 138% FPL
    n_adults = 1 + is_joint.astype(int)
    adults_eligible = np.where(fpl_ratio < adult_cap, n_adults, 0)

    # Aged: if primary or secondary is 65+ and below 100% FPL.
    # Coarse — uses age fields when available.
    age_primary = df.get("primary_agep", pd.Series(0, index=df.index)).fillna(0).to_numpy()
    age_secondary = df.get("secondary_agep", pd.Series(0, index=df.index)).fillna(0).to_numpy()
    aged_count = (
        (age_primary >= p.aged_threshold_age).astype(int)
        + (age_secondary >= p.aged_threshold_age).astype(int)
    )
    aged_eligible = np.where(fpl_ratio < aged_cap, aged_count, 0)

    # Children: dependents count where below 313% FPL
    children_eligible = np.where(fpl_ratio < child_cap, n_dep, 0)

    # Avoid double-counting: aged adults shouldn't also count as adult-expansion
    adults_eligible_net = np.maximum(adults_eligible - aged_eligible, 0)

    annual_value = (
        adults_eligible_net * (p.pmpm_adult_annual * p.adult_pmpm_pct)
        + children_eligible * (p.pmpm_child_annual * p.child_pmpm_pct)
        + aged_eligible * (p.pmpm_aged_disabled_annual * p.aged_pmpm_pct)
    )

    df[out_col] = annual_value
    df[receives_col] = annual_value > 0
    return df
