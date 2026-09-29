"""Federal income tax liability for Hawaii tax units.

A lightweight, year-aware federal income-tax estimator. The full
federal tax code is out of scope for this Hawaii-focused package; the
estimator here is a *closer-than-flat-rate* approximation built to
remove the largest bias in the SPM resource calculation.

The bias being removed: ``poverty/spm.py`` previously used a flat 10%
effective rate × money_income as the federal tax estimate. For Hawaii
SPM-eligible (low-income) filers, the true effective rate after the
standard deduction and nonrefundable credits is typically 0%. The
flat 10% therefore subtracted $0–$2,000+ of phantom federal tax from
SPM resources at the bottom of the income distribution, biasing the
modeled Hawaii poverty rate ~4–6 pp above the Census-published rate.

What this module computes:

    taxable_income = max(0, money_income - federal_standard_deduction)
    tax_before_credits = bracket_schedule(taxable_income, filing_status)
    federal_tax_liability = max(0, tax_before_credits - ctc_nonrefundable)

Refundable credits (EITC, ACTC) are NOT subtracted here — they are
counted as positive SPM resources upstream. This keeps the formula
consistent with Census SPM accounting:

    resources = money_income + EITC + refundable_CTC + ...
              - state_tax - federal_tax_liability - payroll_tax - ...

Year-keyed parameters: standard deduction and bracket schedules for
TY 2022-2026 sourced from IRS Rev. Procs. (TY 2025 standard deduction as
amended by the 2025 reconciliation act, P.L. 119-21 §70102). Inflation
indexing for the SD and bracket thresholds is statutory; later years can be
CPI-extrapolated with ``extrapolate=True``.

:func:`federal_tax_before_credits` exposes the pre-credit bracket tax so the
CTC can be split between its nonrefundable and refundable (ACTC) parts the
way IRC §24(d) orders them.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


# Federal standard deduction by tax year and filing status (IRS Rev. Procs.).
# Used as the principal driver: filers with income < SD owe $0 in federal tax
# regardless of brackets.
_STANDARD_DEDUCTION = {
    2022: {
        "single": 12_950, "married_filing_jointly": 25_900,
        "head_of_household": 19_400, "married_filing_separately": 12_950,
    },
    2023: {
        "single": 13_850, "married_filing_jointly": 27_700,
        "head_of_household": 20_800, "married_filing_separately": 13_850,
    },
    2024: {
        "single": 14_600, "married_filing_jointly": 29_200,
        "head_of_household": 21_900, "married_filing_separately": 14_600,
    },
    # P.L. 119-21 §70102 raised TY 2025 above Rev. Proc. 2024-40's
    # $15,000 / $30,000 / $22,500 (restated in Rev. Proc. 2025-32 §3).
    2025: {
        "single": 15_750, "married_filing_jointly": 31_500,
        "head_of_household": 23_625, "married_filing_separately": 15_750,
    },
    # Rev. Proc. 2025-32 §4.14.
    2026: {
        "single": 16_100, "married_filing_jointly": 32_200,
        "head_of_household": 24_150, "married_filing_separately": 16_100,
    },
}

# Federal income-tax brackets by year and filing status, as
# (upper_threshold, marginal_rate) pairs sorted ascending. The last entry
# uses np.inf to capture the top bracket.
_BRACKETS = {
    2022: {
        "single": [
            (10_275, 0.10), (41_775, 0.12), (89_075, 0.22),
            (170_050, 0.24), (215_950, 0.32), (539_900, 0.35),
            (np.inf, 0.37),
        ],
        "married_filing_jointly": [
            (20_550, 0.10), (83_550, 0.12), (178_150, 0.22),
            (340_100, 0.24), (431_900, 0.32), (647_850, 0.35),
            (np.inf, 0.37),
        ],
        "head_of_household": [
            (14_650, 0.10), (55_900, 0.12), (89_050, 0.22),
            (170_050, 0.24), (215_950, 0.32), (539_900, 0.35),
            (np.inf, 0.37),
        ],
    },
    2023: {
        "single": [
            (11_000, 0.10), (44_725, 0.12), (95_375, 0.22),
            (182_100, 0.24), (231_250, 0.32), (578_125, 0.35),
            (np.inf, 0.37),
        ],
        "married_filing_jointly": [
            (22_000, 0.10), (89_450, 0.12), (190_750, 0.22),
            (364_200, 0.24), (462_500, 0.32), (693_750, 0.35),
            (np.inf, 0.37),
        ],
        "head_of_household": [
            (15_700, 0.10), (59_850, 0.12), (95_350, 0.22),
            (182_100, 0.24), (231_250, 0.32), (578_100, 0.35),
            (np.inf, 0.37),
        ],
    },
    2024: {
        "single": [
            (11_600, 0.10), (47_150, 0.12), (100_525, 0.22),
            (191_950, 0.24), (243_725, 0.32), (609_350, 0.35),
            (np.inf, 0.37),
        ],
        "married_filing_jointly": [
            (23_200, 0.10), (94_300, 0.12), (201_050, 0.22),
            (383_900, 0.24), (487_450, 0.32), (731_200, 0.35),
            (np.inf, 0.37),
        ],
        "head_of_household": [
            (16_550, 0.10), (63_100, 0.12), (100_500, 0.22),
            (191_950, 0.24), (243_700, 0.32), (609_350, 0.35),
            (np.inf, 0.37),
        ],
    },
    2025: {
        "single": [
            (11_925, 0.10), (48_475, 0.12), (103_350, 0.22),
            (197_300, 0.24), (250_525, 0.32), (626_350, 0.35),
            (np.inf, 0.37),
        ],
        "married_filing_jointly": [
            (23_850, 0.10), (96_950, 0.12), (206_700, 0.22),
            (394_600, 0.24), (501_050, 0.32), (751_600, 0.35),
            (np.inf, 0.37),
        ],
        "head_of_household": [
            (17_000, 0.10), (64_850, 0.12), (103_350, 0.22),
            (197_300, 0.24), (250_500, 0.32), (626_350, 0.35),
            (np.inf, 0.37),
        ],
    },
    # Rev. Proc. 2025-32 §4.01, Tables 1-3.
    2026: {
        "single": [
            (12_400, 0.10), (50_400, 0.12), (105_700, 0.22),
            (201_775, 0.24), (256_225, 0.32), (640_600, 0.35),
            (np.inf, 0.37),
        ],
        "married_filing_jointly": [
            (24_800, 0.10), (100_800, 0.12), (211_400, 0.22),
            (403_550, 0.24), (512_450, 0.32), (768_700, 0.35),
            (np.inf, 0.37),
        ],
        "head_of_household": [
            (17_700, 0.10), (67_450, 0.12), (105_700, 0.22),
            (201_750, 0.24), (256_200, 0.32), (640_600, 0.35),
            (np.inf, 0.37),
        ],
    },
}


def _federal_parameters_for_year(tax_year: int, *, extrapolate: bool = False):
    """Return ``(standard_deduction_by_status, brackets_by_status)``.

    With ``extrapolate=True``, years past the last published Rev. Proc. scale
    the latest year's dollar amounts by ``CREDIT_PARAM_CPI_GROWTH`` (the same
    chained-CPI assumption the credit modules use), rounding down to $50 for
    the standard deduction (§63(c)(4)) and $25 for bracket thresholds
    (§1(f)(7)). Raises ``KeyError`` otherwise.
    """
    if tax_year in _STANDARD_DEDUCTION:
        return _STANDARD_DEDUCTION[tax_year], _BRACKETS[tax_year]
    last = max(_STANDARD_DEDUCTION)
    if extrapolate and tax_year > last:
        from tax_modeler.credits.eitc import CREDIT_PARAM_CPI_GROWTH

        factor = (1.0 + CREDIT_PARAM_CPI_GROWTH) ** (tax_year - last)
        sd = {k: float(v * factor // 50 * 50) for k, v in _STANDARD_DEDUCTION[last].items()}
        brackets = {
            k: [(u if np.isinf(u) else float(u * factor // 25 * 25), r) for u, r in b]
            for k, b in _BRACKETS[last].items()
        }
        return sd, brackets
    raise KeyError(
        f"Federal tax parameters not defined for tax year {tax_year}. "
        f"Supported years: {sorted(_STANDARD_DEDUCTION)}"
        + ("" if extrapolate else " (pass extrapolate=True for later years)")
    )


def _tax_from_brackets(taxable: np.ndarray, brackets: list) -> np.ndarray:
    """Compute federal tax by walking through the marginal brackets."""
    tax = np.zeros_like(taxable, dtype=float)
    prev_threshold = 0.0
    for upper, rate in brackets:
        slice_lo = prev_threshold
        slice_hi = upper
        in_slice = np.clip(taxable - slice_lo, 0.0, slice_hi - slice_lo)
        tax += in_slice * rate
        prev_threshold = upper
        if np.isinf(upper):
            break
    return tax


# ---------------------------------------------------------------------------
# Federal (tax-return) income from PUMS income
# ---------------------------------------------------------------------------

# Share of PUMS gross wages that appears as W-2 box 1 wages. ACS WAGP is
# gross pay; box 1 excludes pre-tax deductions (Hawaii ERS contributions of
# 7.8-9.8%, 401(k)/403(b)/457 deferrals, §125 health premiums), and survey
# reports run somewhat high. Calibrated so PUMS 2020-24 wages (deflated to
# 2022 dollars, before the EITC reweight) in the $1-$500K federal-AGI bands
# equal IRS SOI Hawaii TY 2022 A00200 for those bands ($33.65B; file
# 22in55cmcsv). The $500K+ bands are excluded because PUMS top-codes wages.
# One rate for all earners: low earners likely defer less, so it slightly
# understates their earnings.
FEDERAL_W2_WAGE_FACTOR = 0.87

# IRC §86 base / adjusted-base amounts (not indexed).
_SS_BASE = {"married_filing_jointly": 32_000}
_SS_ADJUSTED_BASE = {"married_filing_jointly": 44_000}
_SS_BASE_OTHER, _SS_ADJUSTED_BASE_OTHER = 25_000, 34_000


def taxable_social_security(
    other_income: np.ndarray, benefits: np.ndarray, filing_status: np.ndarray,
) -> np.ndarray:
    """IRC §86 taxable Social Security. MFS filers use $0 base amounts
    (the rule for spouses who lived together)."""
    status = np.asarray(filing_status, dtype=str)
    b1 = np.array([_SS_BASE.get(s, _SS_BASE_OTHER) for s in status], dtype=float)
    b2 = np.array([_SS_ADJUSTED_BASE.get(s, _SS_ADJUSTED_BASE_OTHER) for s in status], dtype=float)
    mfs = status == "married_filing_separately"
    b1[mfs] = 0.0
    b2[mfs] = 0.0
    provisional = other_income + 0.5 * benefits
    tier1 = np.minimum(0.5 * benefits, 0.5 * np.maximum(provisional - b1, 0.0))
    tier2 = np.minimum(
        0.85 * benefits,
        0.85 * np.maximum(provisional - b2, 0.0) + np.minimum(0.5 * benefits, 0.5 * (b2 - b1)),
    )
    return np.where(provisional > b2, tier2, tier1)


def add_federal_income_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``federal_agi`` and ``federal_earned_income``.

    ``income`` (the model's AGI proxy, also Hawaii AGI) counts gross wages
    and 85% of Social Security for everyone. The federal columns instead use
    W-2 wages (``FEDERAL_W2_WAGE_FACTOR`` × PUMS wages) and §86 taxable
    Social Security. Federal credits and federal tax read these when present;
    Hawaii tax keeps reading ``income``.
    """
    out = df.copy()
    n = len(out)

    def _col(name: str) -> np.ndarray:
        if name in out.columns:
            return out[name].fillna(0).astype(float).to_numpy()
        return np.zeros(n)

    wages = np.maximum(_col("primary_wagp"), 0) + np.maximum(_col("secondary_wagp"), 0)
    wage_cut = (1.0 - FEDERAL_W2_WAGE_FACTOR) * wages
    if "primary_ssp_full" in out.columns or "secondary_ssp_full" in out.columns:
        benefits = _col("primary_ssp_full") + _col("secondary_ssp_full")
        in_income = 0.85 * benefits
    else:
        in_income = _col("primary_ssp") + _col("secondary_ssp")
        benefits = in_income / 0.85
    income = out["income"].fillna(0).astype(float).to_numpy() if "income" in out.columns else np.zeros(n)
    other = income - wage_cut - in_income
    status = out["filing_status"].astype(str).to_numpy() if "filing_status" in out.columns else np.full(n, "single")
    out["federal_agi"] = other + taxable_social_security(other, benefits, status)
    earned = _col("earned_income") if "earned_income" in out.columns else income
    out["federal_earned_income"] = np.maximum(earned - wage_cut, 0.0)
    return out


def likely_nonfiler(df: pd.DataFrame, *, tax_year: int, extrapolate: bool = False) -> pd.Series:
    """Units with federal AGI under the standard deduction and no earnings.

    They have no filing requirement and no refundable credit to claim, so
    IRS return counts omit them. For comparisons against SOI only; credit
    and tax calculations do not use it.
    """
    sd_by_status, _ = _federal_parameters_for_year(tax_year, extrapolate=extrapolate)
    agi = df["federal_agi"] if "federal_agi" in df.columns else df["income"]
    ei = df["federal_earned_income"] if "federal_earned_income" in df.columns else df.get("earned_income", 0)
    sd = df["filing_status"].astype(str).map(lambda s: sd_by_status.get(s, sd_by_status["single"]))
    return (agi.fillna(0) < sd) & (pd.Series(ei, index=df.index).fillna(0) <= 0)


def federal_tax_before_credits(
    income,
    filing_status,
    *,
    tax_year: int,
    extrapolate: bool = False,
) -> np.ndarray:
    """Bracket tax on ``max(0, income - standard deduction)``, before credits.

    ``income`` and ``filing_status`` are scalars or equal-length arrays.
    MFS and unknown statuses use the single schedule, as in
    :func:`compute_federal_income_tax_for_units`.
    """
    sd_by_status, brackets_by_status = _federal_parameters_for_year(
        tax_year, extrapolate=extrapolate,
    )
    income = np.atleast_1d(np.asarray(income, dtype=float))
    status = np.atleast_1d(np.asarray(filing_status, dtype=str))
    if status.size == 1 and income.size > 1:
        status = np.repeat(status, income.size)
    sd = np.array(
        [sd_by_status.get(s, sd_by_status["single"]) for s in status],
        dtype=float,
    )
    taxable = np.maximum(np.nan_to_num(income) - sd, 0.0)
    tax = np.zeros(income.size, dtype=float)
    for s in np.unique(status):
        key = s if s in brackets_by_status else "single"
        mask = status == s
        tax[mask] = _tax_from_brackets(taxable[mask], brackets_by_status[key])
    return tax


def compute_federal_income_tax_for_units(
    units: pd.DataFrame,
    *,
    tax_year: int,
    out_col: str = "federal_tax_liability",
    income_col: Optional[str] = None,
    filing_status_col: str = "filing_status",
    ctc_nonrefundable_col: str = "ctc_nonrefundable",
    extrapolate: bool = False,
) -> pd.DataFrame:
    """Add a ``federal_tax_liability`` column to ``units``.

    Liability is computed as::

        taxable_income     = max(0, money_income - federal_standard_deduction)
        tax_before_credits = federal_brackets(taxable_income, filing_status)
        federal_tax        = max(0, tax_before_credits - ctc_nonrefundable)

    The bracket schedule (10/12/22/24/32/35/37%) and standard deductions
    follow IRS Rev. Procs. for the requested tax year. MFS is mapped to
    the single brackets / SD for filers who file separately on their own.

    Refundable credits (EITC, ACTC) are NOT subtracted here. They are
    counted as positive SPM resources upstream. ``ctc_nonrefundable`` is
    the part of CTC that can offset tax but is not paid out as a refund.
    If the column is missing, it is treated as zero (an overstatement
    of tax for filers with kids, since their CTC will be entirely
    refundable up to the bracket-implied limit). The TY-aware federal
    pipeline (``_recalculate_ctc``) populates this column.

    Parameters
    ----------
    tax_year:
        One of {2022, ..., 2026}, or later with ``extrapolate=True``.
    income_col:
        Defaults to ``federal_agi`` when present (see
        :func:`add_federal_income_columns`), else ``total_cash_income``.

    Returns
    -------
    pd.DataFrame
        Copy of ``units`` with a new ``federal_tax_liability`` column
        (non-negative annual dollars).
    """
    out = units.copy()
    if income_col is None:
        income_col = "federal_agi" if "federal_agi" in out.columns else "total_cash_income"
    tax = federal_tax_before_credits(
        out[income_col].fillna(0).astype(float).to_numpy(),
        out[filing_status_col].astype(str).to_numpy(),
        tax_year=tax_year,
        extrapolate=extrapolate,
    )

    # Nonrefundable CTC offsets bracket tax, but cannot push it below zero.
    if ctc_nonrefundable_col in out.columns:
        ctc_nonref = out[ctc_nonrefundable_col].fillna(0).astype(float).to_numpy()
        tax = np.maximum(tax - ctc_nonref, 0.0)

    out[out_col] = tax
    return out


__all__ = [
    "FEDERAL_W2_WAGE_FACTOR",
    "add_federal_income_columns",
    "compute_federal_income_tax_for_units",
    "federal_tax_before_credits",
    "likely_nonfiler",
    "taxable_social_security",
]
