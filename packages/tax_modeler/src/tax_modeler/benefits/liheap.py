"""LIHEAP (Low Income Home Energy Assistance Program).

Federal block grant administered in Hawaii by HI DHS Benefit, Employment
and Support Services Division (BESSD). Eligibility: 150% FPL. The HI
allocation is small (~$1.5-2M/year) and serves ~6,000-8,000 households
at an average benefit of ~$200-300/year (cooling-assistance focus given
HI climate).

Phase 5 simplifications:

  * Flat per-eligible-household benefit (no utility-cost imputation —
    ACS HINCP/RNTP could provide proxy data; deferred).
  * No prioritization rules (real LIHEAP prioritizes elderly and
    disabled when funds are limited; Phase 4 take-up imputation
    corrects against the BESSD caseload).
  * Households allocated equally — caller can scale via
    ``benefit_amount_pct`` to reflect block-grant shortfall years.

Reform DSL (``Reform.benefit_overrides["liheap"]``):

  * ``benefit_amount_pct``       multiplier on per-household benefit
  * ``income_threshold_factor``   multiplier on 150% FPL
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ._fpl import fpl_year_for, hawaii_fpl

_LIHEAP_BENEFIT_ANNUAL_2024 = 250.0


@dataclass(frozen=True)
class LiheapParameters:
    income_fpl_cap: float = 1.50
    annual_benefit: float = _LIHEAP_BENEFIT_ANNUAL_2024
    benefit_amount_pct: float = 1.0
    income_threshold_factor: float = 1.0


def hawaii_liheap_parameters() -> LiheapParameters:
    return LiheapParameters()


def with_liheap_overrides(
    base: LiheapParameters, overrides: Mapping[str, object] | None
) -> LiheapParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown LIHEAP override keys: {sorted(bad)}", available=sorted(valid)
        )
    return replace(base, **dict(overrides))


def compute_liheap_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: LiheapParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "liheap_amount",
) -> pd.DataFrame:
    """Compute LIHEAP annual benefit per tax unit (flat amount if eligible).

    ``tax_year`` selects the FPL table used for the income test (default
    2024). Pass the run's tax year on forward-projected runs so the
    eligibility threshold matches the same-year income basis.
    """
    p = with_liheap_overrides(params or hawaii_liheap_parameters(), overrides)
    df = units.copy()

    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
    hh_size = 1 + is_joint.astype(int) + n_dep
    income = df["income"].fillna(0).astype(float).to_numpy()
    fpl = np.array([hawaii_fpl(fpl_year_for(tax_year), household_size=int(s)) for s in hh_size])
    fpl_ratio = np.where(fpl > 0, income / fpl, 0.0)

    cap = p.income_fpl_cap * p.income_threshold_factor
    eligible = fpl_ratio < cap
    df[out_col] = np.where(
        eligible, p.annual_benefit * p.benefit_amount_pct, 0.0
    )
    return df
