"""Federal housing assistance (Section 8 vouchers + public housing).

HUD-funded programs administered in Hawaii by Hawaii Public Housing
Authority (HPHA) and several local PHAs. The two main vehicles:

  * **Housing Choice Voucher (HCV / Section 8)** — household pays 30%
    of adjusted AGI in rent; HUD pays the difference up to a
    locally-set "payment standard" (~Fair Market Rent).
  * **Public Housing** — same 30%-of-AGI rent rule applied to publicly-
    owned units.

For fiscal-impact purposes we model both as a single in-kind transfer:

    benefit = max(0, FMR − 0.30 × AGI)

where FMR is HUD's Fair Market Rent for Hawaii (2024 Honolulu MSA
2-bedroom: ~$2,500/month = $30,000/year).

Income eligibility: 50% AMI for "very low income" (the standard HCV
threshold). HUD Hawaii Area Median Income tables are the production
source; we approximate via FPL since AMI tracks regional median income
and HHS Hawaii FPL captures the Hawaii cost-of-living adjustment.

Phase 5 simplifications:

  * Single FMR used statewide (Honolulu MSA 2BR). Real PHAs set
    payment standards by ZIP and bedroom count; Phase 5 doesn't
    differentiate.
  * Voucher waitlists are not modeled — Phase 4 take-up imputation
    against the HUD CHAS database calibrates the receiving headcount.
  * "Adjusted AGI" approximated as ``income`` (the deductions HUD
    allows — dependents, elderly, etc. — would shave a few percentage
    points off the rent calculation).

Reform DSL (``Reform.benefit_overrides["housing"]``):

  * ``payment_standard_pct``     multiplier on FMR (default 1.0)
  * ``income_threshold_factor``   multiplier on 50% AMI / FPL proxy
  * ``tenant_rent_pct``          tenant rent share of AGI (default 0.30)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ._fpl import fpl_year_for, hawaii_fpl

# 2024 HUD Honolulu MSA 2BR Fair Market Rent annualized.
# Source: HUD Office of Policy Development & Research FMR tables.
_FMR_HONOLULU_2BR_ANNUAL_2024 = 30_000.0
# 50% of HHS Hawaii FPL × ~3.5 ≈ rough HUD "very-low-income" income limit.
# In production replace with HUD AMI tables loaded by household size.
_HOUSING_FPL_PROXY_CAP = 3.50


@dataclass(frozen=True)
class HousingParameters:
    fmr_annual: float = _FMR_HONOLULU_2BR_ANNUAL_2024
    income_fpl_cap: float = _HOUSING_FPL_PROXY_CAP
    tenant_rent_pct: float = 0.30
    payment_standard_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_housing_parameters() -> HousingParameters:
    return HousingParameters()


def with_housing_overrides(
    base: HousingParameters, overrides: Mapping[str, object] | None
) -> HousingParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown housing override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


def compute_housing_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: HousingParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "housing_subsidy_amount",
) -> pd.DataFrame:
    """Compute imputed housing-assistance annual benefit per tax unit.

    benefit = max(0, FMR × payment_standard_pct − tenant_rent_pct × income)
    when the unit is below the income cap (proxied via FPL).

    ``tax_year`` selects the FPL table used for the income test (default
    2024). Pass the run's tax year on forward-projected runs so the
    eligibility threshold matches the same-year income basis.
    """
    p = with_housing_overrides(params or hawaii_housing_parameters(), overrides)
    df = units.copy()

    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
    hh_size = 1 + is_joint.astype(int) + n_dep
    income = df["income"].fillna(0).astype(float).to_numpy()
    fpl = np.array([hawaii_fpl(fpl_year_for(tax_year), household_size=int(s)) for s in hh_size])
    fpl_ratio = np.where(fpl > 0, income / fpl, 0.0)

    cap = p.income_fpl_cap * p.income_threshold_factor
    eligible = fpl_ratio < cap

    fmr = p.fmr_annual * p.payment_standard_pct
    tenant_rent = p.tenant_rent_pct * income
    subsidy = np.maximum(fmr - tenant_rent, 0.0)

    df[out_col] = np.where(eligible, subsidy, 0.0)
    return df
