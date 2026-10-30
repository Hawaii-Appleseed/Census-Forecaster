"""WIC (Special Supplemental Nutrition Program for Women, Infants, and Children).

USDA-funded grant program providing food packages, nutrition education,
and breastfeeding support to low-income pregnant/postpartum women,
infants, and children up to age 5. Income test: 185% FPL. Categorical
eligibility extends to participants in SNAP / Medicaid / TANF.

Hawaii participation (FY2024 USDA-FNS State Activity Summary): ~31,000
average monthly participants (women + infants + children combined).

Phase 5 simplifications:

  * Categorical eligibility (women + children <5) is approximated from
    ``num_dependents`` — we count up to ``num_dependents`` children
    plus a "potential mother" slot when there is at least one
    dependent. Pregnancy isn't observable on ACS PUMS; Phase 4
    take-up imputation against the WIC monthly caseload corrects.
  * Per-participant package value: ~$60/month food benefits
    (FY2024 USDA WIC food cost). Annual ≈ $720/participant.

Reform DSL (``Reform.benefit_overrides["wic"]``):

  * ``package_value_pct``        multiplier on per-participant value
  * ``income_threshold_factor``   multiplier on 185% FPL
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ._fpl import fpl_year_for, hawaii_fpl

_WIC_PER_PARTICIPANT_ANNUAL_2024 = 720.0    # ~$60/month USDA-FNS food cost


@dataclass(frozen=True)
class WicParameters:
    income_fpl_cap: float = 1.85
    per_participant_annual: float = _WIC_PER_PARTICIPANT_ANNUAL_2024
    package_value_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_wic_parameters() -> WicParameters:
    return WicParameters()


def with_wic_overrides(
    base: WicParameters, overrides: Mapping[str, object] | None
) -> WicParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown WIC override keys: {sorted(bad)}", available=sorted(valid)
        )
    return replace(base, **dict(overrides))


def compute_wic_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: WicParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "wic_amount",
) -> pd.DataFrame:
    """Compute approximate WIC annual food-package value per tax unit.

    Eligibility: household income < 185% FPL × ``income_threshold_factor``,
    AND the unit has at least one dependent (proxy for woman/infant/child).
    Number of WIC participants per unit = number of dependents (each
    counted once as either an infant, child <5, or via the categorically
    eligible mother slot).

    ``tax_year`` selects the FPL table used for the income test (default
    2024). Pass the run's tax year on forward-projected runs so the
    eligibility threshold matches the same-year income basis.
    """
    p = with_wic_overrides(params or hawaii_wic_parameters(), overrides)
    df = units.copy()

    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
    hh_size = 1 + is_joint.astype(int) + n_dep
    income = df["income"].fillna(0).astype(float).to_numpy()
    fpl = np.array([hawaii_fpl(fpl_year_for(tax_year), household_size=int(s)) for s in hh_size])
    fpl_ratio = np.where(fpl > 0, income / fpl, 0.0)

    cap = p.income_fpl_cap * p.income_threshold_factor
    income_eligible = fpl_ratio < cap
    has_kids = n_dep > 0
    eligible = income_eligible & has_kids

    n_participants = np.where(eligible, n_dep, 0)
    df[out_col] = n_participants * p.per_participant_annual * p.package_value_pct
    return df
