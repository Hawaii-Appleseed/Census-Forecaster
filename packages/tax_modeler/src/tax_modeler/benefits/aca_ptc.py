"""ACA Premium Tax Credit (PTC).

The PTC is a refundable federal tax credit that subsidizes Marketplace
health-insurance premiums for households with income up to 400% FPL
(the cap was suspended through 2025 by ARPA/IRA, raising effective
eligibility above 400% FPL when the benchmark premium exceeds
``applicable_pct × income``). Hawaii uses the federal exchange
(HealthCare.gov) since the dissolution of the Hawaii Health Connector
in 2017.

Calculation (annual, simplified):

    expected_contribution = applicable_pct(income/FPL) × income
    PTC = max(0, benchmark_silver_premium − expected_contribution)

The applicable-percentage schedule comes from IRC §36B and the ARPA/IRA
revisions. We use the IRA-extended 2023-2025 schedule (sliding from 0%
at <150% FPL to 8.5% above 400% FPL, with no hard cliff).

Phase 5 simplifications:

  * Benchmark silver premium is approximated from the Hawaii state-
    average bronze premium (~$5,800/year/individual in 2024) scaled by
    a household-size factor. Actual benchmarks vary by ZIP and age;
    HealthCare.gov publishes state-level averages annually.
  * Eligibility excludes Medicaid-eligible households (handled by
    setting PTC to zero when Medicaid receives is True, if the column
    is present). When the Medicaid column is absent, we apply the PTC
    income test alone — slight overcount for Medicaid-eligible
    households is documented; Phase 6 cross-check vs PolicyEngine US
    will quantify.
  * No employer-sponsored-insurance offer test (ACS doesn't report it
    cleanly). Households with income >250% FPL who would otherwise be
    ESI-covered may be slightly over-imputed.

Reform DSL (``Reform.benefit_overrides["aca_ptc"]``):

  * ``credit_pct``                multiplier on PTC dollars (default 1.0)
  * ``income_threshold_factor``   multiplier on % FPL caps (default 1.0)
  * ``benchmark_premium_pct``     multiplier on benchmark silver premium (default 1.0)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

from ._fpl import fpl_year_for, hawaii_fpl

# 2024 HI Marketplace average silver-plan premium per individual (annual).
# Approximate; actual benchmark varies by age + ZIP. HHS Marketplace Open
# Enrollment Public Use File is the production source.
_BENCHMARK_SILVER_PREMIUM_INDIVIDUAL_2024 = 5_800.0
# Per-additional-member scaling factor applied to the individual benchmark.
_BENCHMARK_HOUSEHOLD_SCALE = {1: 1.0, 2: 1.95, 3: 2.45, 4: 2.95}
_BENCHMARK_INCREMENT_PER_EXTRA = 0.40


def _benchmark_premium(hh_size: int, base_individual: float) -> float:
    if hh_size <= 0:
        return 0.0
    if hh_size in _BENCHMARK_HOUSEHOLD_SCALE:
        return base_individual * _BENCHMARK_HOUSEHOLD_SCALE[hh_size]
    largest = max(_BENCHMARK_HOUSEHOLD_SCALE)
    return base_individual * (
        _BENCHMARK_HOUSEHOLD_SCALE[largest]
        + (hh_size - largest) * _BENCHMARK_INCREMENT_PER_EXTRA
    )


def _applicable_percentage(fpl_ratio: float) -> float:
    """ARPA/IRA-extended applicable percentage schedule (2023-2025)."""
    if fpl_ratio < 1.50:
        return 0.0
    if fpl_ratio < 2.00:
        return np.interp(fpl_ratio, [1.50, 2.00], [0.0, 0.02])
    if fpl_ratio < 2.50:
        return np.interp(fpl_ratio, [2.00, 2.50], [0.02, 0.04])
    if fpl_ratio < 3.00:
        return np.interp(fpl_ratio, [2.50, 3.00], [0.04, 0.06])
    if fpl_ratio < 4.00:
        return np.interp(fpl_ratio, [3.00, 4.00], [0.06, 0.085])
    return 0.085  # Capped above 400% FPL


@dataclass(frozen=True)
class AcaPtcParameters:
    benchmark_silver_premium_individual: float = _BENCHMARK_SILVER_PREMIUM_INDIVIDUAL_2024
    medicaid_excluded_below_fpl: float = 1.38   # 138% FPL Medicaid-expansion floor
    credit_pct: float = 1.0
    income_threshold_factor: float = 1.0
    benchmark_premium_pct: float = 1.0


def aca_ptc_parameters() -> AcaPtcParameters:
    return AcaPtcParameters()


def with_ptc_overrides(
    base: AcaPtcParameters, overrides: Mapping[str, object] | None
) -> AcaPtcParameters:
    if not overrides:
        return base
    valid = set(base.__dataclass_fields__)
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown ACA PTC override keys: {sorted(bad)}", available=sorted(valid)
        )
    return replace(base, **dict(overrides))


def compute_aca_ptc_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int = 2024,
    params: AcaPtcParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "aca_ptc_amount",
    medicaid_col: str = "medicaid_receives",
) -> pd.DataFrame:
    """Add an annual ACA Premium Tax Credit column to ``units``.

    Per-unit calculation: count household members (filer + spouse +
    dependents), compute %FPL based on HHS Hawaii guidelines, look up
    applicable percentage, subtract expected contribution from benchmark
    silver premium.

    ``tax_year`` selects the FPL table used for the %FPL computation
    (default 2024). Pass the run's tax year on forward-projected runs so
    the eligibility thresholds match the same-year income basis.

    Categorical exclusions (each tightens eligibility when the relevant
    column is present, populated by ``enrich_for_benefits``):

      * ``n_medicaid_covered > 0`` — anyone on the unit is on Medicaid
        (HINS3==1) → ineligible. Per ACA, Medicaid-eligible households
        cannot also claim PTC.
      * ``n_medicare_covered > 0`` — Medicare (HINS4==1) coverage on
        the primary filer / spouse → ineligible.
      * ``n_employer_insured >= filer count`` — when employer-sponsored
        insurance (HINS1==1) is offered to every filer the unit is
        treated as having an "ESI offer" and PTC is unavailable.
      * ``medicaid_col`` (legacy receives indicator) — back-compat path.

    When none of the rich columns are present, falls back to the FPL
    threshold proxy (138% Medicaid floor).
    """
    p = with_ptc_overrides(params or aca_ptc_parameters(), overrides)
    df = units.copy()

    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
    hh_size = 1 + is_joint.astype(int) + n_dep
    income = df["income"].fillna(0).astype(float).to_numpy()

    fpl = np.array([hawaii_fpl(fpl_year_for(tax_year), household_size=int(s)) for s in hh_size])
    fpl_ratio = np.where(fpl > 0, income / fpl, 0.0)

    applicable = np.array([_applicable_percentage(r) for r in fpl_ratio])
    benchmark = np.array([
        _benchmark_premium(int(s), p.benchmark_silver_premium_individual) for s in hh_size
    ]) * p.benchmark_premium_pct

    expected_contribution = applicable * income
    ptc = np.maximum(0.0, benchmark - expected_contribution)

    # Use rich PUMS-derived columns when available; fall back to FPL proxy
    has_rich = "n_medicaid_covered" in df.columns
    if has_rich:
        n_medicaid = df["n_medicaid_covered"].fillna(0).astype(int).to_numpy()
        n_medicare = df.get("n_medicare_covered", pd.Series(0, index=df.index)).fillna(0).astype(int).to_numpy()
        n_employer = df.get("n_employer_insured", pd.Series(0, index=df.index)).fillna(0).astype(int).to_numpy()
        # Filer count: 1 + spouse on MFJ
        n_filers = 1 + is_joint.astype(int)
        eligible = (n_medicaid == 0) & (n_medicare == 0) & (n_employer < n_filers)
    else:
        medicaid_threshold = p.medicaid_excluded_below_fpl * p.income_threshold_factor
        above_medicaid = fpl_ratio >= medicaid_threshold
        if medicaid_col in df.columns:
            not_on_medicaid = ~df[medicaid_col].astype(bool).to_numpy()
            eligible = above_medicaid & not_on_medicaid
        else:
            eligible = above_medicaid

    df[out_col] = np.where(eligible, ptc * p.credit_pct, 0.0)
    return df
