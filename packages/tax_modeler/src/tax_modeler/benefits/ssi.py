"""Federal Supplemental Security Income (SSI) — Hawaii applies federal rules.

SSI is a federal cash benefit for aged (65+), blind, or disabled people
with limited income and resources. The 2024 federal benefit rate (FBR)
is $943/month for an individual and $1,415/month for an eligible couple.

Phase 3 implementation choices:

  * **Eligibility heuristic:** SSI eligibility is determined per-person,
    requiring age/disability flags + income + resources. The synthetic
    fixture has age (``primary_agep`` / ``secondary_agep``) but no
    disability flag, so we proxy by **age 65+** at the tax-unit level.
    Tax units where the primary filer is 65+ AND the unit's income is
    below an FBR-derived monthly threshold are treated as eligible. This
    underestimates SSI take-up among working-age disabled adults; Phase
    4 take-up imputation calibrates against the SSA administrative
    caseload to correct.
  * **Federal payment only.** Hawaii's state supplement is in
    :mod:`tax_modeler.benefits.ssi_hi_supplement` so reforms can target
    one without the other.
  * **No retrospective / earlier-month proration.** Annual amount =
    monthly FBR × 12. Real SSI prorates partial months, but for fiscal
    impact analysis the annual approximation is standard.

Reform DSL knobs (``Reform.benefit_overrides["ssi"]``):

  * ``federal_payment_pct``     multiplier on FBR (default 1.0)
  * ``income_threshold_factor`` multiplier on the income disregard (default 1.0)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

# 2024 federal benefit rates (monthly)
_FBR_INDIVIDUAL_2024 = 943.0
_FBR_COUPLE_2024 = 1_415.0
# Approximate countable-income disregard: SSI excludes the first $20 of
# unearned income and the first $65 of earned income (plus 50% of the
# remainder), but for tax-unit-level filtering we use a coarse annualized
# threshold equal to the FBR × 12 (anyone whose income exceeds that is
# fully phased out).
_INDIVIDUAL_ANNUAL_INCOME_LIMIT_2024 = _FBR_INDIVIDUAL_2024 * 12
_COUPLE_ANNUAL_INCOME_LIMIT_2024 = _FBR_COUPLE_2024 * 12


@dataclass(frozen=True)
class SsiParameters:
    fbr_individual_monthly: float = _FBR_INDIVIDUAL_2024
    fbr_couple_monthly: float = _FBR_COUPLE_2024
    individual_income_limit_annual: float = _INDIVIDUAL_ANNUAL_INCOME_LIMIT_2024
    couple_income_limit_annual: float = _COUPLE_ANNUAL_INCOME_LIMIT_2024
    aged_threshold: int = 65
    federal_payment_pct: float = 1.0
    income_threshold_factor: float = 1.0


def federal_ssi_parameters() -> SsiParameters:
    return SsiParameters()


def with_ssi_overrides(
    base: SsiParameters, overrides: Mapping[str, object] | None
) -> SsiParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown SSI override keys: {sorted(bad)}", available=sorted(valid)
        )
    return replace(base, **dict(overrides))


def compute_ssi_for_units(
    units: pd.DataFrame,
    *,
    params: SsiParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "ssi_amount",
) -> pd.DataFrame:
    """Add a federal SSI annual-amount column to ``units``.

    Eligibility: aged (>= aged_threshold) OR disabled (``n_disabled`` > 0
    when that column is present, e.g. after ``enrich_for_benefits``)
    AND annualized unit income below the (couple/individual) income limit.

    Returns a copy of ``units`` with ``out_col`` populated.
    """
    p = with_ssi_overrides(params or federal_ssi_parameters(), overrides)
    df = units.copy()

    # Age column: primary_agep (always populated post-construction); fall back
    # to a column named "primary_age" for non-PUMS-derived inputs.
    if "primary_agep" in df.columns:
        age = df["primary_agep"].fillna(0).astype(float).to_numpy()
    elif "primary_age" in df.columns:
        age = df["primary_age"].fillna(0).astype(float).to_numpy()
    else:
        # No age data — no one qualifies
        df[out_col] = 0.0
        return df

    income = df["income"].fillna(0).astype(float).to_numpy()
    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()

    couple_limit = p.couple_income_limit_annual * p.income_threshold_factor
    individual_limit = p.individual_income_limit_annual * p.income_threshold_factor
    income_limit = np.where(is_joint, couple_limit, individual_limit)

    is_aged = age >= p.aged_threshold
    # Disability: use n_disabled when enrich_for_benefits has run; else fall
    # back to age-only (the prior heuristic).
    if "n_disabled" in df.columns:
        is_disabled = df["n_disabled"].fillna(0).astype(int).to_numpy() > 0
    else:
        is_disabled = np.zeros(len(df), dtype=bool)
    eligible = (is_aged | is_disabled) & (income < income_limit)

    fbr_annual = np.where(
        is_joint,
        p.fbr_couple_monthly * 12,
        p.fbr_individual_monthly * 12,
    ) * p.federal_payment_pct

    # Crude phase-out: linear from full benefit at $0 income to zero at the limit.
    # SSI's actual disregard math is more nuanced but produces a similar shape
    # at this granularity.
    phaseout_factor = np.clip(1.0 - (income / np.maximum(income_limit, 1.0)), 0.0, 1.0)
    df[out_col] = np.where(eligible, fbr_annual * phaseout_factor, 0.0)
    return df
