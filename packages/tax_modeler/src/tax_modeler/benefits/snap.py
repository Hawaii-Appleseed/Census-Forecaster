"""SNAP (Supplemental Nutrition Assistance Program) for Hawaii.

Hawaii uses elevated income thresholds and maximum benefit allotments
compared to the contiguous United States, set by USDA-FNS each fiscal
year (HI is one of three "high-cost" jurisdictions alongside Alaska,
Guam, and the US Virgin Islands).

The model here covers the standard SNAP arithmetic at *household* level:

    1. Gross income test:  household monthly income < 130% FPL × HI factor
    2. Net income test:    income after deductions < 100% FPL × HI factor
    3. Benefit calculation: max_benefit(hh_size) − 0.30 × net_income

Results are aggregated up by ``hh_id`` (PUMS SERIALNO) from the per-tax-
unit input frame, then distributed back to constituent tax units in
proportion to household membership counts.

Phase-3 simplifications (documented for the maintenance log):

  * Standard deduction, earnings deduction, dependent-care deduction, and
    excess shelter cap use FY2024 USDA tables, applied uniformly. Hawaii-
    specific shelter expenses are not yet imputed (would need housing-
    cost data from the ACS HINCP/RNTP fields, deferred to Phase 5).
  * "Categorically eligible" households (e.g. all-SSI / TANF) are not
    flagged separately — the gross/net tests on simulated income do most
    of the work. Take-up imputation in Phase 4 will correct any over-
    or under-counting against the DHS administrative caseload.
  * Asset tests are not modeled. Hawaii uses Broad-Based Categorical
    Eligibility, which effectively waives the federal asset test for
    most applicants — so omitting it is conservative.

Reform DSL knobs (under ``Reform.benefit_overrides["snap"]``):

  * ``max_benefit_pct``         multiplier on USDA max benefit (default 1.0)
  * ``gross_income_limit_factor`` multiplier on 130% FPL (default 1.0)
  * ``net_income_limit_factor``   multiplier on 100% FPL (default 1.0)
  * ``benefit_reduction_rate``    benefit phaseout rate (default 0.30)
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

# ---------------------------------------------------------------------------
# Hawaii FY2024 USDA-FNS SNAP parameters (FY = Oct 2023 - Sep 2024)
#
# Sources (verify when promoting to production):
#   USDA-FNS "Hawaii Maximum Allowable Income & Maximum Allotments" tables
#   https://fns-prod.azureedge.us/sites/default/files/resource-files/...
#
# Numbers below are the published HI fiscal-year maximums for a household
# of 1..8 people. Households >8 add a flat per-person increment.
# ---------------------------------------------------------------------------

_HI_MAX_BENEFIT_BY_SIZE_FY2024 = {
    1: 359, 2: 658, 3: 942, 4: 1_197,
    5: 1_421, 6: 1_706, 7: 1_885, 8: 2_154,
}
_HI_MAX_BENEFIT_INCREMENT_PER_EXTRA_PERSON_FY2024 = 269

# Federal poverty guidelines (HI elevated, monthly) — FY2024 USDA SNAP
# 130% FPL gross-income limits for 1..8 persons. Larger households add
# a flat per-person increment (the published USDA table).
_HI_GROSS_INCOME_LIMIT_BY_SIZE_FY2024 = {
    1: 1_973, 2: 2_667, 3: 3_361, 4: 4_055,
    5: 4_749, 6: 5_443, 7: 6_137, 8: 6_831,
}
_HI_GROSS_INCOME_INCREMENT_PER_EXTRA_PERSON_FY2024 = 694
_HI_NET_INCOME_LIMIT_BY_SIZE_FY2024 = {
    k: int(v / 1.30) for k, v in _HI_GROSS_INCOME_LIMIT_BY_SIZE_FY2024.items()
}
_HI_NET_INCOME_INCREMENT_PER_EXTRA_PERSON_FY2024 = int(
    _HI_GROSS_INCOME_INCREMENT_PER_EXTRA_PERSON_FY2024 / 1.30
)

# Standard deduction (HI) — FY2024 USDA-FNS published table
_HI_STANDARD_DEDUCTION_BY_SIZE_FY2024 = {
    1: 235, 2: 235, 3: 235, 4: 250,
    5: 293, 6: 336,
}
# Households >6 use the size-6 amount

# Earnings deduction: 20% of gross earned income
_EARNINGS_DEDUCTION_RATE = 0.20


@dataclass(frozen=True)
class SnapParameters:
    """Per-reform SNAP parameter set. Multipliers default to 1.0 (no change)."""

    max_benefit_by_size: Mapping[int, int] = field(
        default_factory=lambda: dict(_HI_MAX_BENEFIT_BY_SIZE_FY2024)
    )
    max_benefit_increment_per_extra_person: int = (
        _HI_MAX_BENEFIT_INCREMENT_PER_EXTRA_PERSON_FY2024
    )
    gross_income_limit_by_size: Mapping[int, int] = field(
        default_factory=lambda: dict(_HI_GROSS_INCOME_LIMIT_BY_SIZE_FY2024)
    )
    gross_income_increment_per_extra_person: int = (
        _HI_GROSS_INCOME_INCREMENT_PER_EXTRA_PERSON_FY2024
    )
    net_income_limit_by_size: Mapping[int, int] = field(
        default_factory=lambda: dict(_HI_NET_INCOME_LIMIT_BY_SIZE_FY2024)
    )
    net_income_increment_per_extra_person: int = (
        _HI_NET_INCOME_INCREMENT_PER_EXTRA_PERSON_FY2024
    )
    standard_deduction_by_size: Mapping[int, int] = field(
        default_factory=lambda: dict(_HI_STANDARD_DEDUCTION_BY_SIZE_FY2024)
    )
    earnings_deduction_rate: float = _EARNINGS_DEDUCTION_RATE
    benefit_reduction_rate: float = 0.30
    # Reform-DSL multipliers (applied at the end of the calc)
    max_benefit_pct: float = 1.0
    gross_income_limit_factor: float = 1.0
    net_income_limit_factor: float = 1.0


def hawaii_snap_parameters() -> SnapParameters:
    """Return the default Hawaii FY2024 SNAP parameters."""
    return SnapParameters()


def with_snap_overrides(
    base: SnapParameters, overrides: Mapping[str, object] | None
) -> SnapParameters:
    """Apply Reform-DSL overrides to a SnapParameters baseline."""
    if not overrides:
        return base
    valid = {f for f in base.__dataclass_fields__}
    bad = set(overrides) - valid
    if bad:
        raise ConfigError(
            f"unknown SNAP override keys: {sorted(bad)}",
            available=sorted(valid),
        )
    return replace(base, **dict(overrides))


# ---------------------------------------------------------------------------
# Computation helpers
# ---------------------------------------------------------------------------


def _lookup_by_size(table: Mapping[int, int], increment: int, size: int) -> int:
    """Return ``table[size]`` for table sizes 1..max, else extrapolate linearly."""
    if size <= 0:
        return 0
    max_in_table = max(table)
    if size <= max_in_table:
        return int(table[size])
    return int(table[max_in_table] + (size - max_in_table) * increment)


def _household_size_from_units(group: pd.DataFrame) -> int:
    """Number of household members across all tax units sharing an hh_id.

    Counts: primary filer + (spouse if MFJ) + dependents, summed across
    every tax unit in the household. Uses ``num_dependents`` on the units
    DataFrame (always present after ``enrich_for_credits``).
    """
    n_filers = int(len(group))
    n_spouses = int((group["filing_status"] == "married_filing_jointly").sum())
    n_dependents = int(group["num_dependents"].fillna(0).sum())
    return n_filers + n_spouses + n_dependents


def _compute_household_snap(
    gross_income_monthly: float,
    earned_income_monthly: float,
    hh_size: int,
    params: SnapParameters,
) -> float:
    """Return monthly SNAP benefit for a household. Zero if ineligible."""
    if hh_size <= 0:
        return 0.0

    gross_limit = _lookup_by_size(
        params.gross_income_limit_by_size,
        params.gross_income_increment_per_extra_person,
        hh_size,
    ) * params.gross_income_limit_factor
    if gross_income_monthly > gross_limit:
        return 0.0

    std_ded = _lookup_by_size(params.standard_deduction_by_size, 0, min(hh_size, 6))
    earnings_ded = earned_income_monthly * params.earnings_deduction_rate
    net_income = max(0.0, gross_income_monthly - std_ded - earnings_ded)

    net_limit = _lookup_by_size(
        params.net_income_limit_by_size,
        params.net_income_increment_per_extra_person,
        hh_size,
    ) * params.net_income_limit_factor
    if net_income > net_limit:
        return 0.0

    max_benefit = _lookup_by_size(
        params.max_benefit_by_size,
        params.max_benefit_increment_per_extra_person,
        hh_size,
    ) * params.max_benefit_pct
    benefit = max(0.0, max_benefit - params.benefit_reduction_rate * net_income)
    return float(benefit)


# ---------------------------------------------------------------------------
# Public DataFrame helper
# ---------------------------------------------------------------------------


def compute_snap_for_units(
    units: pd.DataFrame,
    *,
    params: SnapParameters | None = None,
    overrides: Mapping[str, object] | None = None,
    out_col: str = "snap_amount",
    months: int = 12,
) -> pd.DataFrame:
    """Add an annual SNAP-benefit column to ``units``.

    Aggregates household income at ``hh_id`` (PUMS SERIALNO) granularity,
    computes monthly SNAP, multiplies by ``months`` (default 12 = annual),
    then distributes back to constituent tax units in proportion to
    member counts (filer + spouse + dependents).

    Parameters
    ----------
    units:
        Tax-unit DataFrame with at least: ``hh_id`` or ``SERIALNO``,
        ``filing_status``, ``income``, ``earned_income``, ``num_dependents``.
    params:
        Baseline parameters. Defaults to :func:`hawaii_snap_parameters`.
    overrides:
        Reform-DSL overrides applied on top of ``params``.
    out_col:
        Name of the column to write.
    months:
        Number of months to multiply by (12 = annual; 1 = monthly).

    Returns
    -------
    pd.DataFrame
        Copy of ``units`` with the new column. Per-unit SNAP is the
        household's total annual benefit times the unit's share of the
        household. Sums to the household total when re-aggregated.
    """
    if "hh_id" in units.columns:
        hh_col = "hh_id"
    elif "SERIALNO" in units.columns:
        hh_col = "SERIALNO"
    else:
        raise ConfigError(
            "compute_snap_for_units requires 'hh_id' or 'SERIALNO' column"
        )

    p = with_snap_overrides(params or hawaii_snap_parameters(), overrides)
    df = units.copy()

    # Per-unit member counts (filer + spouse if MFJ + dependents)
    is_joint = (df["filing_status"] == "married_filing_jointly").astype(int)
    df["_unit_members"] = (
        1 + is_joint + df["num_dependents"].fillna(0).astype(int)
    )

    snap_per_row = np.zeros(len(df))
    for _hh, group in df.groupby(hh_col, sort=False):
        hh_size = _household_size_from_units(group)
        gross_monthly = float(group["income"].fillna(0).sum()) / 12.0
        earned_monthly = float(group.get("earned_income", group["income"]).fillna(0).sum()) / 12.0
        monthly_snap = _compute_household_snap(gross_monthly, earned_monthly, hh_size, p)
        annual_hh_snap = monthly_snap * months
        if annual_hh_snap <= 0 or hh_size <= 0:
            continue
        # Allocate proportionally to per-unit member counts
        members = group["_unit_members"].to_numpy()
        total_members = members.sum()
        if total_members <= 0:
            continue
        shares = members / total_members
        snap_per_row[group.index] = annual_hh_snap * shares

    df[out_col] = snap_per_row
    df.drop(columns=["_unit_members"], inplace=True)
    return df
