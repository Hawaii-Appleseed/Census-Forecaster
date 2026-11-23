"""SNAP (Supplemental Nutrition Assistance Program) for Hawaiʻi.

Hawaiʻi has its own maximum allotments, deductions and poverty guidelines, set by USDA-FNS each federal fiscal year
(October to September), and broad-based categorical eligibility: a gross income limit of 200 percent of poverty, no
asset test and, since February 2025, no net income test. :func:`hawaii_snap_parameters` has fiscal years 2024 to 2027.

Household benefit (:func:`_compute_household_snap`): gross test, then
``max_allotment(size) - 0.30 * net income``, where net income is gross less the standard deduction and 20 percent of
earnings, with the minimum benefit for households of one or two that pass the gross test.

Not modeled: the excess shelter, dependent-care, medical and child-support deductions (the ACS does not carry the
expenses as SNAP counts them), so the formula understates benefits for households with high housing costs. Who
receives SNAP and the total paid come from the survey and the State instead: :func:`calibrate_snap_receipt` takes
the households that report SNAP, draws eligible non-reporters to the Department of Human Services' caseload, and
scales the total to its issuance.

Reform DSL knobs (under ``Reform.benefit_overrides["snap"]``):

  * ``max_benefit_pct``           multiplier on the maximum allotment (default 1.0)
  * ``gross_income_limit_factor`` multiplier on the gross limit (default 1.0)
  * ``net_income_limit_factor``   multiplier on the net limit (default 1.0; only where the net test applies)
  * ``benefit_reduction_rate``    benefit phaseout rate (default 0.30)

Sources: USDA-FNS cost-of-living adjustment memos for fiscal years 2025 (August 2, 2024), 2026 (August 14, 2025)
and 2027 (August 21, 2026); fiscal year 2024 allotments and standard deductions as recorded in the FY2024 SNAP
Quality Control file (``data/snap_qc/``), poverty guidelines from HHS (2023, Hawaiʻi). Until November 2026 this
module carried tables labeled FY2024 that were not Hawaiʻi's (a one-person maximum of $359 against $527).
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

# ---------------------------------------------------------------------------
# Hawaiʻi SNAP parameters by federal fiscal year
#
# allotments: households of 1..8, then each additional person; standard deduction: sizes 1..6 (6+); poverty: the
# monthly 100 percent guideline (the net income standard) for households of 1..4, then each additional person; the
# gross limit under categorical eligibility is twice it.
# ---------------------------------------------------------------------------

_HI_TABLES: dict[int, dict] = {
    2024: {"allotments": (527, 967, 1385, 1759, 2088, 2506, 2770, 3166), "extra": 396,
           "standard": {1: 279, 2: 279, 3: 279, 4: 279, 5: 280, 6: 321},
           "poverty": ((1398, 1890, 2383, 2875), 493), "minimum": 42, "net_test": True},
    2025: {"allotments": (517, 948, 1357, 1723, 2046, 2456, 2714, 3102), "extra": 388,
           "standard": {1: 287, 2: 287, 3: 287, 4: 287, 5: 292, 6: 335},
           "poverty": ((1443, 1959, 2475, 2990), 516), "minimum": 41, "net_test": False},
    2026: {"allotments": (506, 929, 1334, 1689, 2010, 2415, 2668, 3040), "extra": 371,
           "standard": {1: 295, 2: 295, 3: 295, 4: 295, 5: 300, 6: 344},
           "poverty": ((1500, 2027, 2555, 3082), 527), "minimum": 41, "net_test": False},
    2027: {"allotments": (496, 910, 1307, 1655, 1969, 2367, 2615, 2979), "extra": 364,
           "standard": {1: 306, 2: 306, 3: 306, 4: 306, 5: 309, 6: 354},
           "poverty": ((1530, 2075, 2619, 3163), 545), "minimum": 40, "net_test": False},
}
SNAP_FISCAL_YEARS = tuple(sorted(_HI_TABLES))
GROSS_LIMIT_PCT_OF_POVERTY = 2.0          # broad-based categorical eligibility

# Earnings deduction: 20% of gross earned income
_EARNINGS_DEDUCTION_RATE = 0.20


def snap_fiscal_year(year: int) -> int:
    """The table year to use for ``year``: itself if tabled, else the nearest end of the tables."""
    return min(max(int(year), SNAP_FISCAL_YEARS[0]), SNAP_FISCAL_YEARS[-1])


def _sized(table: tuple[float, ...], extra: float, factor: float = 1.0, n: int = 8) -> dict[int, int]:
    """Sizes 1..n from a table for the first sizes and a per-person step after it, times ``factor``."""
    return {k: int(round(factor * (table[k - 1] if k <= len(table) else table[-1] + extra * (k - len(table)))))
            for k in range(1, n + 1)}


@dataclass(frozen=True)
class SnapParameters:
    """Per-reform SNAP parameter set (Hawaiʻi, one fiscal year). Multipliers default to 1.0 (no change)."""

    max_benefit_by_size: Mapping[int, int] = field(
        default_factory=lambda: dict(enumerate(_HI_TABLES[SNAP_FISCAL_YEARS[-1]]["allotments"], start=1))
    )
    max_benefit_increment_per_extra_person: int = _HI_TABLES[SNAP_FISCAL_YEARS[-1]]["extra"]
    gross_income_limit_by_size: Mapping[int, int] = field(
        default_factory=lambda: _sized(*_HI_TABLES[SNAP_FISCAL_YEARS[-1]]["poverty"], GROSS_LIMIT_PCT_OF_POVERTY)
    )
    gross_income_increment_per_extra_person: int = int(round(
        GROSS_LIMIT_PCT_OF_POVERTY * _HI_TABLES[SNAP_FISCAL_YEARS[-1]]["poverty"][1]))
    net_income_limit_by_size: Mapping[int, int] = field(
        default_factory=lambda: _sized(*_HI_TABLES[SNAP_FISCAL_YEARS[-1]]["poverty"])
    )
    net_income_increment_per_extra_person: int = _HI_TABLES[SNAP_FISCAL_YEARS[-1]]["poverty"][1]
    standard_deduction_by_size: Mapping[int, int] = field(
        default_factory=lambda: dict(_HI_TABLES[SNAP_FISCAL_YEARS[-1]]["standard"])
    )
    earnings_deduction_rate: float = _EARNINGS_DEDUCTION_RATE
    benefit_reduction_rate: float = 0.30
    min_benefit: int = _HI_TABLES[SNAP_FISCAL_YEARS[-1]]["minimum"]
    min_benefit_max_size: int = 2
    net_income_test: bool = _HI_TABLES[SNAP_FISCAL_YEARS[-1]]["net_test"]
    fiscal_year: int = SNAP_FISCAL_YEARS[-1]
    # Reform-DSL multipliers (applied at the end of the calc)
    max_benefit_pct: float = 1.0
    gross_income_limit_factor: float = 1.0
    net_income_limit_factor: float = 1.0


def hawaii_snap_parameters(fiscal_year: int | None = None) -> SnapParameters:
    """Hawaiʻi's SNAP parameters for a federal fiscal year (default: the latest tabled), clipped to the tables."""
    fy = SNAP_FISCAL_YEARS[-1] if fiscal_year is None else snap_fiscal_year(fiscal_year)
    t = _HI_TABLES[fy]
    first, extra = t["poverty"]
    return SnapParameters(
        max_benefit_by_size=dict(enumerate(t["allotments"], start=1)),
        max_benefit_increment_per_extra_person=t["extra"],
        gross_income_limit_by_size=_sized(first, extra, GROSS_LIMIT_PCT_OF_POVERTY),
        gross_income_increment_per_extra_person=int(round(GROSS_LIMIT_PCT_OF_POVERTY * extra)),
        net_income_limit_by_size=_sized(first, extra),
        net_income_increment_per_extra_person=extra,
        standard_deduction_by_size=dict(t["standard"]),
        min_benefit=t["minimum"],
        net_income_test=t["net_test"],
        fiscal_year=fy,
    )


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

    if params.net_income_test:
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
    if hh_size <= params.min_benefit_max_size:
        benefit = max(benefit, params.min_benefit * params.max_benefit_pct)
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


# ---------------------------------------------------------------------------
# Who receives SNAP, and how much in all
# ---------------------------------------------------------------------------

_QC_UNITS = Path(__file__).resolve().parents[1] / "data" / "snap_qc" / "hawaii_qc_fy2024_units.csv"


def qc_average_benefit_by_size(fiscal_year: int | None = None) -> dict[int, float]:
    """Average monthly benefit of Hawaiʻi SNAP households by size (1 to 6+), from the FY2024 SNAP Quality Control
    file, restated to ``fiscal_year``'s maximum allotments."""
    import pandas as pd

    q = pd.read_csv(_QC_UNITS, usecols=["FYWGT", "FSUSIZE", "FSBEN"])
    p0, p1 = hawaii_snap_parameters(2024), hawaii_snap_parameters(fiscal_year)
    ratio = [(_lookup_by_size(p1.max_benefit_by_size, p1.max_benefit_increment_per_extra_person, int(n))
              / _lookup_by_size(p0.max_benefit_by_size, p0.max_benefit_increment_per_extra_person, int(n)))
             for n in q["FSUSIZE"]]
    size = q["FSUSIZE"].clip(upper=6)
    avg = (q["FYWGT"] * q["FSBEN"] * ratio).groupby(size).sum() / q["FYWGT"].groupby(size).sum()
    return {int(k): float(v) for k, v in avg.items()}


@dataclass(frozen=True)
class SnapReceipt:
    """How :func:`calibrate_snap_receipt` placed SNAP: per household and in total."""

    # one row per household: hh_id, snap (monthly, before scaling), income, earned, weight, reported, n_persons,
    # recipient
    households: object
    dollar_scale: float
    reported: float               # weighted households that report SNAP (all kept)
    reported_ineligible: float    # of which fail the gross test on annual income (given the size average)
    imputed: float                # eligible non-reporters drawn to reach the caseload
    draw_rate: float              # the probability each eligible non-reporter was drawn with


def _hash_uniform(keys, salt: str) -> np.ndarray:
    """A uniform number in [0, 1) per key, the same on every run."""
    import hashlib

    return np.array([int(hashlib.md5(f"{salt}_{k}".encode()).hexdigest()[:8], 16) / 0x100000000 for k in keys])


def calibrate_snap_receipt(
    units: pd.DataFrame,
    *,
    target_households: float,
    target_dollars: float,
    params: SnapParameters | None = None,
    fallback_benefit_by_size: Mapping[int, float] | None = None,
    reported_col: str = "hh_snap_reported",
    hh_weight_col: str = "hh_weight",
    hh_size_col: str = "hh_n_persons",
    out_col: str = "snap_amount",
) -> tuple[pd.DataFrame, SnapReceipt]:
    """Annual SNAP on tax units: who from the survey, how much from the rules, the level from the State.

    Every household that reports SNAP (``reported_col``, the ACS's ``FS`` = 1) is a recipient. Its monthly benefit
    is :func:`compute_snap_for_units` under ``params``; a reporting household that fails the gross test on its
    annual survey income (income of members outside the SNAP unit, months off SNAP) gets
    ``fallback_benefit_by_size[min(n, 6)]`` instead (default: :func:`qc_average_benefit_by_size`). The survey
    misses about a third of recipients, so households that do not report SNAP but are eligible are each drawn, with
    one probability, until the weighted count (``hh_weight_col``) reaches ``target_households``. The draw hashes the
    household id, so it is the same on every run. Every benefit is then scaled by one factor so the annual total
    equals ``target_dollars``, and spread over the household's tax units by the module's member shares.

    Units without ``reported_col`` are treated as not reporting, so every recipient is drawn. Returns the units with
    ``out_col`` (annual) and the :class:`SnapReceipt`.
    """
    prm = params or hawaii_snap_parameters()
    fallback = fallback_benefit_by_size or qc_average_benefit_by_size(prm.fiscal_year)
    if "hh_id" not in units.columns:
        raise ConfigError("calibrate_snap_receipt requires an 'hh_id' column")
    u = compute_snap_for_units(units, params=prm, months=1, out_col=out_col)
    hid = u["hh_id"].astype(str)
    earned = u["earned_income"] if "earned_income" in u.columns else u["income"]
    reported = (u[reported_col].fillna(False).astype(bool) if reported_col in u.columns
                else pd.Series(False, index=u.index))
    first = ~hid.duplicated()
    hh = pd.DataFrame({
        "snap": u[out_col].groupby(hid).sum(),
        "income": u["income"].fillna(0).groupby(hid).sum(),
        "earned": earned.fillna(0).groupby(hid).sum(),
        "weight": u.loc[first, hh_weight_col].set_axis(hid[first]) if hh_weight_col in u.columns
        else u["weight"].groupby(hid).first(),
        "reported": reported.groupby(hid).max(),
        "n_persons": (u.loc[first, hh_size_col].set_axis(hid[first]) if hh_size_col in u.columns
                      else (1 + (u["filing_status"] == "married_filing_jointly").astype(int)
                            + u["num_dependents"].fillna(0).astype(int)).groupby(hid).sum()),
    })
    hh["n_persons"] = hh["n_persons"].fillna(1).clip(lower=1).astype(int)
    formula = hh["snap"].copy()
    eligible = formula > 0
    rep = hh["reported"].astype(bool)
    swap = rep & ~eligible
    hh.loc[swap, "snap"] = [float(fallback[int(min(n, 6))]) for n in hh.loc[swap, "n_persons"]]
    hh["recipient"] = rep.copy()
    reported_w = float(hh.loc[rep, "weight"].sum())
    pool = eligible & ~rep
    pool_w = float(hh.loc[pool, "weight"].sum())
    rate = min(1.0, max(0.0, (target_households - reported_w) / pool_w)) if pool_w > 0 else 0.0
    drawn = pool & (_hash_uniform(hh.index, "snap_takeup") < rate)
    hh.loc[drawn, "recipient"] = True
    monthly = float((hh["weight"] * hh["snap"] * hh["recipient"]).sum())
    if monthly <= 0:
        raise ConfigError("no SNAP recipients to scale")
    scale = target_dollars / (12.0 * monthly)
    f = hid.map(formula).to_numpy(dtype=float)
    share = np.where(f > 0, u[out_col].to_numpy(dtype=float) / np.where(f > 0, f, 1.0),
                     1.0 / hid.map(hid.value_counts()).to_numpy(dtype=float))
    keep = hid.map(hh["recipient"]).fillna(False).to_numpy(dtype=bool)
    u[out_col] = np.where(keep, hid.map(hh["snap"]).to_numpy(dtype=float) * share * 12.0 * scale, 0.0)
    u["snap_recipient_hh"] = keep
    return u, SnapReceipt(
        households=hh.rename_axis("hh_id").reset_index(), dollar_scale=scale, reported=reported_w,
        reported_ineligible=float(hh.loc[swap, "weight"].sum()), imputed=float(hh.loc[drawn, "weight"].sum()),
        draw_rate=rate,
    )
