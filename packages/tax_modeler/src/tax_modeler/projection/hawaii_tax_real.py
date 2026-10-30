"""Real Hawaii income-tax function loaded from the bundled multi-year bracket table.

Phase D replaces the hand-calibrated 3-bracket surrogate in
``revenue_backtest.py`` with Hawaii's actual piecewise-linear schedule
from the bundled ``hawaii_tax_brackets_master_all.csv`` file.

Two bracket vintages are bundled
---------------------------------
* **2018 vintage** — 12 brackets per filing status, effective for tax years
  up through 2024.  These are the brackets in effect since ~2016 (HB 493).
* **2025 vintage** — same 12 brackets but with doubled thresholds per Act 46
  (2023 SLH, effective TY2025 and later).  E.g. the top $200k threshold
  becomes $325k for single filers.

Standard deductions also changed
----------------------------------
* TY ≤ 2023:   single $2,200 / joint $4,400 / HoH $3,212
* TY 2024-2025: single $4,400 / joint $8,800 / HoH $6,424
* TY 2026+:    further increases per Act 46 phase-in schedule

Filing statuses
---------------
``Single_Married_Separate``, ``Joint_Surviving_Spouse``, ``Head_of_Household``

Household proxy ("weighted") function
--------------------------------------
B19013 median household income conflates all filing statuses.  The
``hawaii_tax_weighted`` function applies a weighted average across filing
statuses using approximate DOTAX SOI 2022 distribution weights:

  * Single / MFS ≈ 52 %
  * Joint / QW   ≈ 37 %
  * HoH          ≈ 11 %

This provides a better proxy for the aggregate revenue impact of a
1-household income change than single-status application alone.

DOTAX 2022 calibration context
--------------------------------
At the 2022 Honolulu ACS median ($83,737, B19013), the weighted function
yields an effective rate near 6.5% (before credits).  DOTAX Table 12A
(``DotaxTable12AValidator``) reports an average $4,751 for the $75k–$100k
AGI bracket (before credits, 54,976 resident returns).  The weighted
function at $83,737 returns ~$5,500–$5,600.

The gap (~15%) reflects: (a) the average AGI within the $75k–$100k bracket
is below $83,737, and (b) itemized deduction heterogeneity lowers average
taxable income.  Bracket structure (progressive lower bands) is correctly
modelled; absolute level matching requires the full income distribution.

Comparison with surrogate
--------------------------
The 3-bracket surrogate in :mod:`revenue_backtest` uses a flat 4% rate on
$0–$48k AGI.  The real schedule has 1.4%/3.2%/5.5%/6.4%/6.8%/7.2%/7.6%
on the lower bands, accumulating to $3,214 (single) at the $48k threshold
vs the surrogate's $1,920 (=$48k×4%).  The real function is significantly
more progressive in the lower-income range and correctly captures the
effective rate near the Hawaii median income.

Usage
-----
::

    from tax_modeler.projection.hawaii_tax_real import (
        hawaii_tax_weighted,
        hawaii_marginal_weighted,
    )
    from tax_modeler.projection.revenue_backtest import run_revenue_backtest

    # Use real brackets as drop-in replacements for the surrogate:
    summaries = run_revenue_backtest(
        series_by_key,
        anchors=eval_anchors,
        tax_fn=hawaii_tax_weighted,
        marginal_fn=hawaii_marginal_weighted,
    )

    # Or call directly for a specific tax year (e.g. 2026 projection):
    rev = hawaii_tax_real(income, tax_year=2026, filing_status="Single_Married_Separate")
"""
from __future__ import annotations

import csv
import functools
import logging
import math
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# File paths
# ---------------------------------------------------------------------------

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
_BRACKETS_CSV = _DATA_DIR / "hawaii_tax_brackets_master_all.csv"
_DEDUCTIONS_CSV = _DATA_DIR / "hawaii_standard_deductions_by_year.csv"

# ---------------------------------------------------------------------------
# Filing status constants
# ---------------------------------------------------------------------------

FS_SINGLE = "Single_Married_Separate"
FS_JOINT = "Joint_Surviving_Spouse"
FS_HOH = "Head_of_Household"
ALL_FILING_STATUSES: tuple[str, ...] = (FS_SINGLE, FS_JOINT, FS_HOH)

# Default household-proxy weights: approximate DOTAX SOI 2022 resident
# filing-status distribution.  Weights are normalized internally.
DEFAULT_WEIGHTS: dict[str, float] = {
    FS_SINGLE: 0.52,
    FS_JOINT:  0.37,
    FS_HOH:    0.11,
}

# Bracket vintages available in the CSV.
_AVAILABLE_BRACKET_YEARS: tuple[int, ...] = (2018, 2025, 2027, 2029)


# ---------------------------------------------------------------------------
# Data loading (CSV-only, no pandas; cached for the process lifetime)
# ---------------------------------------------------------------------------

# Internal type alias: list of (lo, hi, rate, base_tax, base_income) tuples
_BracketList = list[tuple[float, float, float, float, float]]


@functools.lru_cache(maxsize=1)
def _load_brackets() -> dict[tuple[int, str], _BracketList]:
    """Load all bracket schedules from the bundled CSV.

    Returns a dict mapping ``(bracket_year, filing_status)`` to a sorted
    list of ``(lo, hi, rate, base_tax, base_income)`` tuples, where
    ``hi = math.inf`` for the top bracket.
    """
    out: dict[tuple[int, str], _BracketList] = {}
    with open(_BRACKETS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # The CSV includes alternative legislative scenarios for future years
            # (e.g. hb2306_hd1, sb3125_sd1 for 2027).  Only load the base
            # enacted schedule (scenario == '').
            if row.get("scenario", "").strip():
                continue
            yr = int(row["year"])
            fs = row["filing_status"]
            lo = float(row["income_min"])
            hi_raw = row.get("income_max", "")
            hi = math.inf if hi_raw in ("inf", "", "Inf") else float(hi_raw)
            rate = float(row["rate"]) / 100.0
            base_tax = float(row["base_tax"])
            base_income = float(row["base_income"])
            key = (yr, fs)
            out.setdefault(key, []).append((lo, hi, rate, base_tax, base_income))
    # Ensure each schedule is sorted by low end of bracket
    for key in out:
        out[key].sort(key=lambda t: t[0])
    logger.debug("Loaded %d bracket schedules from %s", len(out), _BRACKETS_CSV.name)
    return out


@functools.lru_cache(maxsize=1)
def _load_deductions() -> list[tuple[int, dict[str, float]]]:
    """Load the standard deduction schedule from the bundled CSV.

    Returns a list of ``(year, {filing_status: deduction_amount})`` tuples
    sorted ascending by year.  Use :func:`_deduction_for_year` to query.
    """
    schedule: list[tuple[int, dict[str, float]]] = []
    with open(_DEDUCTIONS_CSV, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            yr = int(row["Year"])
            amounts = {
                FS_JOINT:  float(row["Joint_Surviving_Spouse"]),
                FS_HOH:    float(row["Head_of_Household"]),
                FS_SINGLE: float(row["Single_Married_Separate"]),
            }
            schedule.append((yr, amounts))
    schedule.sort(key=lambda t: t[0])
    logger.debug(
        "Loaded deduction schedule for %d years from %s",
        len(schedule), _DEDUCTIONS_CSV.name,
    )
    return schedule


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _bracket_year(tax_year: int) -> int:
    """Map a tax year to the correct bracket vintage.

    Returns the largest ``_AVAILABLE_BRACKET_YEARS`` entry that is ≤
    ``tax_year``.  If ``tax_year`` is earlier than all available vintages,
    returns the oldest available vintage (2018).
    """
    best = _AVAILABLE_BRACKET_YEARS[0]
    for y in _AVAILABLE_BRACKET_YEARS:
        if y <= tax_year:
            best = y
        else:
            break
    return best


def _deduction_for_year(tax_year: int, filing_status: str) -> float:
    """Return the applicable standard deduction for (tax_year, filing_status).

    Uses step-function lookup: the deduction from the last CSV entry with
    ``year ≤ tax_year``.  Defaults to the oldest entry if ``tax_year`` is
    earlier than all entries.
    """
    schedule = _load_deductions()
    if not schedule:
        return 0.0
    result = schedule[0][1].get(filing_status, 0.0)  # oldest as fallback
    for yr, amounts in schedule:
        if yr <= tax_year:
            result = amounts.get(filing_status, result)
        else:
            break
    return result


def _apply_schedule(
    income: float,
    brackets: _BracketList,
    deduction: float,
) -> float:
    """Apply a bracket schedule to pre-deduction income.

    Returns
    -------
    float
        Estimated tax ≥ 0 (before credits).
    """
    taxable = max(income - deduction, 0.0)
    if taxable <= 0.0:
        return 0.0
    for lo, hi, rate, base_tax, base_income in brackets:
        if taxable <= lo:
            # Taxable is below this bracket's floor — shouldn't happen for
            # a sorted schedule unless taxable == lo exactly (boundary).
            break
        if taxable <= hi:
            return base_tax + (taxable - base_income) * rate
    # Fallback to top bracket (handles rounding near bracket edges)
    lo, hi, rate, base_tax, base_income = brackets[-1]
    return base_tax + (taxable - base_income) * rate


def _marginal_rate_schedule(
    income: float,
    brackets: _BracketList,
    deduction: float,
) -> float:
    """Return the marginal rate at ``income`` for the given schedule."""
    taxable = max(income - deduction, 0.0)
    if taxable <= 0.0:
        return 0.0
    for lo, hi, rate, base_tax, base_income in brackets:
        if lo < taxable <= hi:
            return rate
    # If taxable equals exactly a bracket boundary (lo), fall through to
    # the next bracket.  Top bracket catches everything above last lo.
    return brackets[-1][2]


# ---------------------------------------------------------------------------
# Public API — single filing status
# ---------------------------------------------------------------------------

def hawaii_tax_real(
    income: float,
    tax_year: int = 2022,
    filing_status: str = FS_SINGLE,
) -> float:
    """Apply the real Hawaii income-tax schedule to ``income``.

    Uses the 2018-vintage schedule for tax years ≤ 2024 and the Act-46-
    expanded schedule for TY2025+, with the correct standard deduction for
    each period.

    Parameters
    ----------
    income:
        Pre-deduction household income in dollars.
    tax_year:
        Tax year being computed (determines bracket vintage and deduction).
        Default 2022 → uses 2018 brackets and the pre-reform $2,200 deduction
        (single), which are the applicable values for TY2010–2023.
    filing_status:
        One of :data:`FS_SINGLE` (``"Single_Married_Separate"``),
        :data:`FS_JOINT` (``"Joint_Surviving_Spouse"``), or
        :data:`FS_HOH` (``"Head_of_Household"``).

    Returns
    -------
    float
        Estimated tax liability ≥ 0, before credits.
    """
    if income <= 0:
        return 0.0
    byr = _bracket_year(tax_year)
    brackets = _load_brackets().get((byr, filing_status))
    if not brackets:
        logger.warning(
            "No bracket schedule for (year=%d, filing_status=%s); returning 0",
            byr, filing_status,
        )
        return 0.0
    deduction = _deduction_for_year(tax_year, filing_status)
    return _apply_schedule(income, brackets, deduction)


def hawaii_marginal_real(
    income: float,
    tax_year: int = 2022,
    filing_status: str = FS_SINGLE,
) -> float:
    """Marginal tax rate at ``income`` under the real Hawaii schedule.

    Used for delta-method SE propagation::

        se_revenue ≈ |hawaii_marginal_real(income)| × se_income

    Parameters
    ----------
    income, tax_year, filing_status:
        Same as :func:`hawaii_tax_real`.

    Returns
    -------
    float
        Marginal rate in [0, 1].
    """
    if income <= 0:
        return 0.0
    byr = _bracket_year(tax_year)
    brackets = _load_brackets().get((byr, filing_status))
    if not brackets:
        return 0.0
    deduction = _deduction_for_year(tax_year, filing_status)
    return _marginal_rate_schedule(income, brackets, deduction)


# ---------------------------------------------------------------------------
# Public API — household-proxy (filing-status-weighted)
# ---------------------------------------------------------------------------

def hawaii_tax_weighted(
    income: float,
    tax_year: int = 2022,
    weights: dict[str, float] | None = None,
) -> float:
    """Filing-status-weighted Hawaii tax for a household income.

    Computes the weighted average of :func:`hawaii_tax_real` across all
    three filing statuses using approximate DOTAX SOI 2022 distribution
    weights (see :data:`DEFAULT_WEIGHTS`).

    This is a better proxy for the aggregate revenue impact of a
    household income change than applying a single filing status,
    because the B19013 median income conflates all filer types.

    Parameters
    ----------
    income:
        Pre-deduction household income in dollars.
    tax_year:
        Tax year for bracket and deduction lookup.  Default 2022 → uses
        2018-vintage brackets and pre-reform deductions, correct for
        historical backtests (TY2010–2023).  Pass ``tax_year=2025`` for
        Act-46 forward projections.
    weights:
        Dict mapping filing status string → weight.  Need not sum to 1;
        normalized internally.  Defaults to :data:`DEFAULT_WEIGHTS`.

    Returns
    -------
    float
        Weighted-average estimated tax ≥ 0 (before credits).
    """
    if income <= 0:
        return 0.0
    w = weights or DEFAULT_WEIGHTS
    total_w = sum(w.values())
    if total_w <= 0:
        return 0.0
    return sum(
        (wt / total_w) * hawaii_tax_real(income, tax_year=tax_year, filing_status=fs)
        for fs, wt in w.items()
    )


def hawaii_marginal_weighted(
    income: float,
    tax_year: int = 2022,
    weights: dict[str, float] | None = None,
) -> float:
    """Filing-status-weighted marginal rate at ``income``.

    Used for delta-method SE propagation in :func:`run_revenue_backtest`
    when the real bracket schedule is the ``marginal_fn``.

    Parameters
    ----------
    income, tax_year, weights:
        Same as :func:`hawaii_tax_weighted`.

    Returns
    -------
    float
        Weighted-average marginal rate in [0, 1].
    """
    if income <= 0:
        return 0.0
    w = weights or DEFAULT_WEIGHTS
    total_w = sum(w.values())
    if total_w <= 0:
        return 0.0
    return sum(
        (wt / total_w) * hawaii_marginal_real(income, tax_year=tax_year, filing_status=fs)
        for fs, wt in w.items()
    )
