"""Hawaii Child Care Subsidy Program (CCSP / former First-to-Work).

Administered by HI DHS Benefit, Employment and Support Services
Division. Subsidizes child-care costs for low-income working families,
families in education/training, and families participating in TANF.
Income limit: 85% of State Median Income (SMI), roughly equivalent to
~250% FPL for a family of 3 in Hawaii. Subsidies are paid directly to
licensed providers; parent contributes a sliding-scale co-payment.

Phase 5 simplifications:

  * Eligibility: working/in-training proxied by ``earned_income > 0``
    AND has children under age proxy. Without per-person ages, we
    approximate "children under 13" by ``num_dependents`` (slight
    overcount for older dependents; Phase 4 take-up imputation against
    the CCSP caseload corrects).
  * Per-child subsidy: ~$8,000/year (HI 2024 average, weighted across
    age groups and provider types).
  * Parent co-payment: sliding scale 0%-15% of income above 25% SMI.
    Net household subsidy = gross subsidy − co-payment.

Reform DSL (``Reform.benefit_overrides["childcare"]``):

  * ``per_child_subsidy_pct``    multiplier on annual per-child subsidy
  * ``income_threshold_factor``   multiplier on income cap
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ._fpl import fpl_year_for, hawaii_fpl

_CCSP_PER_CHILD_ANNUAL_2024 = 8_000.0
# 85% SMI for HI family of 3 (FY2024) ~ $89,000 — about 250% of FPL
_CCSP_FPL_CAP = 2.50


@dataclass(frozen=True)
class ChildcareParameters:
    income_fpl_cap: float = _CCSP_FPL_CAP
    per_child_annual: float = _CCSP_PER_CHILD_ANNUAL_2024
    copay_rate: float = 0.10        # rough average sliding-scale rate
    copay_income_floor_fpl: float = 0.50   # below 50% FPL → no co-pay
    per_child_subsidy_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_childcare_parameters() -> ChildcareParameters:
    return ChildcareParameters()


def with_childcare_overrides(
    base: ChildcareParameters, overrides: Mapping[str, object] | None
) -> ChildcareParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown childcare override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def compute_childcare_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: ChildcareParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "childcare_amount",
) -> pd.DataFrame:
    """Compute net annual childcare-subsidy value per tax unit.

    ``tax_year`` selects the FPL table used for the income test (default
    2024). Pass the run's tax year on forward-projected runs so the
    eligibility threshold matches the same-year income basis.
    """
    p = with_childcare_overrides(params or hawaii_childcare_parameters(), overrides)
    df = units.copy()

    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
    hh_size = 1 + is_joint.astype(int) + n_dep
    income = df["income"].fillna(0).astype(float).to_numpy()
    earned = df.get("earned_income", df["income"]).fillna(0).astype(float).to_numpy()
    fpl = np.array([hawaii_fpl(fpl_year_for(tax_year), household_size=int(s)) for s in hh_size])
    fpl_ratio = np.where(fpl > 0, income / fpl, 0.0)

    cap = p.income_fpl_cap * p.income_threshold_factor
    # Work test: parent(s) must be working/training. Use WKHP from
    # enrich_for_benefits when available; fall back to earned_income > 0.
    if "primary_hours_worked" in df.columns:
        primary_hrs = df["primary_hours_worked"].fillna(0).astype(float).to_numpy()
        secondary_hrs = df.get("secondary_hours_worked", pd.Series(0, index=df.index)).fillna(0).astype(float).to_numpy()
        is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
        # CCSP: single parents must work; on joint, both must work.
        working = np.where(
            is_joint,
            (primary_hrs > 0) & (secondary_hrs > 0),
            primary_hrs > 0,
        )
    else:
        working = earned > 0
    eligible = (fpl_ratio < cap) & working & (n_dep > 0)

    gross = n_dep * p.per_child_annual * p.per_child_subsidy_pct
    # Co-payment: 0 below copay floor, then copay_rate × (income − floor_dollars)
    floor_dollars = p.copay_income_floor_fpl * fpl
    copay_base = np.maximum(income - floor_dollars, 0.0)
    copay = copay_base * p.copay_rate
    # Cap copay at gross subsidy (you can't pay back more than you got)
    copay = np.minimum(copay, gross)
    net = np.maximum(gross - copay, 0.0)

    df[out_col] = np.where(eligible, net, 0.0)
    return df
