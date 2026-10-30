"""
Hawaii State Income Tax Liability Module

Calculates Hawaii state income tax for tax year 2023.

Hawaii has 12 marginal tax brackets with a top rate of 11%, one of the
highest in the nation. Unlike the federal system, Hawaii still has a
personal exemption ($1,144 per exemption in 2023).

Sources:
  - Hawaii Form N-11 Instructions (2023)
  - Hawaii Tax Table, Schedule Z
  - HRS § 235-51

Limitations / simplifications:
  - AGI proxy: uses the tax unit's `income` field (PINCP * ADJINC), which
    omits above-the-line deductions (IRA, HSA, student loan interest, etc.)
  - Itemized deductions: standard deduction is used by default. Pass
    ``deduction_params`` (from ``scale_deduction_params_for_target_year``) to
    ``calculate_hawaii_tax`` / ``calculate_hawaii_tax_for_units`` to enable
    expected-value itemized deductions (mortgage interest, charitable,
    medical, real estate) with Hawaii's Pease limitation applied.
  - Hawaii tax credits beyond the low-income refundable credit are not modeled.
  - MFS tax units each pay tax on their own income (no income splitting).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from tax_modeler.per_unit import (
    NotVectorizable,
    apply_columns,
    apply_per_unit,
    numeric_column,
    or_zero,
    py_max,
    py_min,
    py_round,
    status_column,
)

# ---------------------------------------------------------------------------
# Itemizing-policy sentinel
# ---------------------------------------------------------------------------
# ``deduction_params=None`` historically meant "skip itemizing" but that's
# also what callers got by accident when they forgot to pass the parameter.
# Since several silent bugs traced to forgotten ``deduction_params``, we now
# require a non-None value for any TY ≥ 2024 (Act 46+) computation.
#
# Two valid choices for callers:
#
#   1. Pass real params from ``scale_deduction_params_for_target_year(year)``
#      to enable expected-value itemized deductions (recommended for
#      revenue forecasts).
#
#   2. Pass ``NO_ITEMIZING`` to explicitly compute SD-only tax. Use only
#      for legacy backtests, sensitivity sweeps, or pre-Act-46 calibrations
#      where the no-itemizing assumption is intentional.
#
# Forgetting the parameter (or passing ``None``) for TY ≥ 2024 is now a
# ValueError at the entry point — caught at call site, not at silent
# numerical drift hours later.

class _NoItemizingSentinel:
    """Singleton sentinel for explicit "compute SD-only tax" requests."""
    __slots__ = ()
    _instance: _NoItemizingSentinel | None = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NO_ITEMIZING"


NO_ITEMIZING = _NoItemizingSentinel()


def _normalize_deduction_params(
    deduction_params: Any, tax_year: int, caller: str,
) -> dict | None:
    """Validate and normalize ``deduction_params`` for tax-computation entry.

    Returns the dict to pass into the per-filer logic (or None for SD-only
    after explicit opt-in). Raises ValueError when a forecast-relevant
    tax_year (≥ 2024) is invoked without an explicit choice.
    """
    if isinstance(deduction_params, _NoItemizingSentinel):
        return None
    if deduction_params is None:
        if tax_year >= 2024:
            raise ValueError(
                f"{caller}(tax_year={tax_year}) requires an explicit "
                "deduction_params choice. Pass either "
                "scale_deduction_params_for_target_year(year) for "
                "itemizing-aware computation, or NO_ITEMIZING (imported "
                "from tax_modeler.liability.hawaii) to deliberately "
                "compute SD-only tax."
            )
        return None
    if not isinstance(deduction_params, dict):
        raise TypeError(
            f"{caller}: deduction_params must be a dict, NO_ITEMIZING, "
            f"or None (TY < 2024 only). Got {type(deduction_params).__name__}."
        )
    return deduction_params


# ---------------------------------------------------------------------------
# Parameters
# ---------------------------------------------------------------------------

@dataclass
class HawaiiTaxParameters:
    """Hawaii income tax parameters for tax year 2023."""

    # Standard deduction amounts by filing status
    standard_deduction: dict[str, float] = field(default_factory=lambda: {
        'single':                    2_200.0,
        'married_filing_jointly':    4_400.0,
        'married_filing_separately': 2_200.0,
        'head_of_household':         3_212.0,
    })

    # Personal exemption per exemption claimed (taxpayer + spouse + each dependent)
    personal_exemption_per_unit: float = 1_144.0

    # Tax brackets: list of (upper_bound, marginal_rate) pairs, from lowest to highest.
    # The last bracket has upper_bound = infinity.
    # Brackets are keyed by filing status.
    brackets: dict[str, list[tuple[float, float]]] = field(default_factory=lambda: {
        'single': [
            (2_400,   0.014),
            (4_800,   0.032),
            (9_600,   0.055),
            (14_400,  0.064),
            (19_200,  0.068),
            (24_000,  0.072),
            (36_000,  0.076),
            (48_000,  0.079),
            (150_000, 0.0825),
            (175_000, 0.09),
            (200_000, 0.10),
            (float('inf'), 0.11),
        ],
        'married_filing_jointly': [
            (4_800,   0.014),
            (9_600,   0.032),
            (19_200,  0.055),
            (28_800,  0.064),
            (38_400,  0.068),
            (48_000,  0.072),
            (72_000,  0.076),
            (96_000,  0.079),
            (300_000, 0.0825),
            (350_000, 0.09),
            (400_000, 0.10),
            (float('inf'), 0.11),
        ],
        'head_of_household': [
            (3_600,   0.014),
            (7_200,   0.032),
            (14_400,  0.055),
            (21_600,  0.064),
            (28_800,  0.068),
            (36_000,  0.072),
            (54_000,  0.076),
            (72_000,  0.079),
            (225_000, 0.0825),
            (262_500, 0.09),
            (300_000, 0.10),
            (float('inf'), 0.11),
        ],
        # MFS uses same brackets as single
        'married_filing_separately': [
            (2_400,   0.014),
            (4_800,   0.032),
            (9_600,   0.055),
            (14_400,  0.064),
            (19_200,  0.068),
            (24_000,  0.072),
            (36_000,  0.076),
            (48_000,  0.079),
            (150_000, 0.0825),
            (175_000, 0.09),
            (200_000, 0.10),
            (float('inf'), 0.11),
        ],
    })

    # The refundable food/excise tax credit (HRS §235-55.85; reported as
    # ``hi_low_income_credit``) is computed from the statutory schedules in
    # tax_modeler.credits.hi_food_excise, year-aware. It is not a parameter here.


HAWAII_2023 = HawaiiTaxParameters()

# HRS §235-51(f): net long-term capital gains taxed at no more than 7.25%.
# SB 3125 CD1 amends §235-51 (ordinary income brackets) only; the CG cap
# is unchanged and applies under both Act 46 and SB 3125 CD1 baselines.
HAWAII_CG_CAP_RATE = 0.0725

# ---------------------------------------------------------------------------
# Year-specific schedules (Act 46 phase-in)
# ---------------------------------------------------------------------------

# Personal exemption per unit, step-function by effective tax year.
# Matches TaxSystemRegistry.PERSONAL_EXEMPTIONS in config/tax_system_config.py.
_PERSONAL_EXEMPTION_SCHEDULE: list[tuple[int, float]] = [
    (2018, 1_144.0),  # pre-Act-46 (applies for TY≤2024)
    (2025, 1_200.0),  # Act 46 phase-in (TY2025+)
]

# Maps liability module filing-status strings → hawaii_tax_real module strings.
_FS_REAL_MAP: dict[str, str] = {
    'single':                    'Single_Married_Separate',
    'married_filing_separately': 'Single_Married_Separate',
    'married_filing_jointly':    'Joint_Surviving_Spouse',
    'head_of_household':         'Head_of_Household',
}


def _personal_exemption_for_year(tax_year: int) -> float:
    """Step-function lookup for personal exemption amount by tax year."""
    best = _PERSONAL_EXEMPTION_SCHEDULE[0][1]
    for yr, amt in _PERSONAL_EXEMPTION_SCHEDULE:
        if yr <= tax_year:
            best = amt
        else:
            break
    return best


# ---------------------------------------------------------------------------
# Deterministic itemized deduction helper
# ---------------------------------------------------------------------------

def _expected_itemized_deductions(
    agi: float,
    filing_status: str,
    deduction_params: dict,
) -> float:
    """Compute expected-value itemized deductions from ``deduction_params``.

    Uses expected values rather than Monte Carlo draws so the result is
    deterministic and suitable for projection pipelines.  Every stochastic
    step in :class:`ItemizedDeductionEstimator` (homeownership probability,
    charitable claim rate, medical claim rate) is replaced by its probability-
    weighted expectation.

    Parameters
    ----------
    agi:
        Filer's Adjusted Gross Income.
    filing_status:
        One of ``'single'``, ``'married_filing_jointly'``,
        ``'married_filing_separately'``, ``'head_of_household'``.
    deduction_params:
        Raw params dict from :func:`tax_modeler.adjustments.itemized_deductions._load_params`
        (or a scaled copy from ``scale_deduction_params_for_target_year``).

    Returns
    -------
    float
        Expected total itemized deductions after Hawaii's Pease limitation,
        before comparing to the standard deduction. Always ≥ 0.
    """
    is_joint = filing_status in ("married_filing_jointly", "qualifying_widow")

    # --- Mortgage interest (expected over homeownership probability) ----------
    ho_cfg = deduction_params["homeownership"]
    if is_joint:
        ho = ho_cfg["joint"]
    elif filing_status == "head_of_household":
        ho = ho_cfg["hoh"]
    else:
        ho = ho_cfg["other"]
    p_own = min(ho["cap"], ho["base"] + (agi / ho["income_scale"]) * ho["slope"])

    mort = deduction_params["mortgage_interest_tiers"]
    base_interest = mort["default_base_interest"]
    for tier in mort["tiers"]:
        if agi < tier["max_agi"]:
            base_interest = tier["base_interest"]
            break
    if is_joint:
        base_interest *= mort.get("joint_multiplier", 1.0)
    expected_mortgage = p_own * base_interest

    # --- Charitable contributions --------------------------------------------
    charit = deduction_params["charitable_giving"]
    giving_rate = charit["default_rate"]
    for tier in charit["rates_by_agi"]:
        if agi < tier["max_agi"]:
            giving_rate = tier["rate"]
            break
    expected_charitable = charit["claim_rate"] * agi * giving_rate

    # --- Medical expenses (non-elderly claim rate as conservative estimate) --
    med = deduction_params["medical_expenses"]
    threshold = agi * med["agi_threshold"]
    expected_medical = med["claim_rate_other"] * max(
        0.0, agi * med["expense_pct_other"] - threshold
    )

    # --- Real estate taxes ---------------------------------------------------
    re = deduction_params["real_estate_taxes"]
    re_amount = re["default_amount"]
    for tier in re["tiers"]:
        if agi < tier["max_agi"]:
            re_amount = tier["amount"]
            break

    total = expected_mortgage + expected_charitable + expected_medical + re_amount

    # --- Hawaii Pease limitation (HRS § 235-2.4(f)) --------------------------
    pease_threshold = 83_400 if filing_status == "married_filing_separately" else 166_800
    if agi > pease_threshold:
        reduction = 0.03 * (agi - pease_threshold)
        total -= min(reduction, 0.80 * total)

    return max(0.0, total)


def _resolve_effective_deduction(
    agi: float,
    filing_status: str,
    standard_deduction: float,
    deduction_params: dict | None,
) -> float:
    """Return the greater of the standard deduction and expected itemized deductions.

    When ``deduction_params`` is ``None``, returns ``standard_deduction`` unchanged
    (preserving the existing "standard deduction always" behaviour for base-year
    calculations).  When params are provided, itemized deductions are computed
    deterministically via :func:`_expected_itemized_deductions` and the larger
    amount is returned.
    """
    eff, _ = _resolve_effective_deduction_pair(
        agi, filing_status, standard_deduction, deduction_params
    )
    return eff


def _resolve_effective_deduction_pair(
    agi: float,
    filing_status: str,
    standard_deduction: float,
    deduction_params: dict | None,
) -> tuple[float, float]:
    """Return ``(effective_deduction, itemized_raw)``.

    ``effective_deduction`` is ``max(standard_deduction, itemized)`` when
    ``deduction_params`` is provided, else ``standard_deduction``.

    ``itemized_raw`` is the expected itemized deduction *before* comparison
    against the standard deduction (always ≥ 0). It is stored separately so
    downstream scenario comparisons can compute ``max(scenario_SD, itemized)``
    correctly when the scenario's standard deduction differs from the one
    used at projection time (e.g. HB 2306 ORIG which freezes the SD at TY
    2026 levels for TY 2027+).
    """
    if deduction_params is None:
        return standard_deduction, 0.0
    itemized = _expected_itemized_deductions(agi, filing_status, deduction_params)
    return max(standard_deduction, itemized), itemized


# ---------------------------------------------------------------------------
# Core calculation
# ---------------------------------------------------------------------------

def calculate_hawaii_tax(
    tax_unit: dict,
    params: HawaiiTaxParameters = HAWAII_2023,
    tax_year: int = 2023,
    deduction_params: Any = None,
    cg_income: float = 0.0,
) -> dict[str, float]:
    """
    Calculate Hawaii state income tax liability for a single tax unit.

    For ``tax_year >= 2024``, brackets and standard deductions are sourced from
    ``projection.hawaii_tax_real`` (Act 46 multi-vintage CSV schedules), so the
    function correctly applies doubled thresholds (TY2025+) and the scheduled
    standard-deduction phase-in.  The 2023 parameters (``params``) are used for
    TY ≤ 2023.

    Args:
        tax_unit: Dictionary with:
            - filing_status: str
            - income: float  (Hawaii AGI proxy — PINCP * ADJINC, total income)
            - num_dependents: int
        params: HawaiiTaxParameters (defaults to 2023; used only for TY ≤ 2023)
        tax_year: Tax year for bracket/deduction vintage selection (default 2023).
        deduction_params: REQUIRED for tax_year ≥ 2024. Pass either:
            * scaled params from ``scale_deduction_params_for_target_year(year)``
              for itemizing-aware computation (recommended for forecasts), or
            * ``NO_ITEMIZING`` (imported from this module) to deliberately
              compute SD-only tax (legacy backtests / sensitivity sweeps).
            For tax_year < 2024 (pre-Act-46), ``None`` is permitted and
            falls back to SD-only.
        cg_income: Net long-term capital gains included in ``income``.
            When non-zero, the HRS §235-51(f) cap is applied: the incremental
            bracket tax attributable to CG income is limited to
            ``HAWAII_CG_CAP_RATE`` (7.25%) × ``cg_income``.  Defaults to 0
            (backward-compatible — all income treated as ordinary).

    Returns:
        Dictionary with:
            - hi_agi: Hawaii Adjusted Gross Income (proxy)
            - hi_standard_deduction: Effective deduction applied (max of SD and itemized)
            - hi_itemized_deduction: Raw expected itemized deduction (≥ 0, before SD floor).
              Used by per_unit_tax to compute scenario-specific max(scenario_SD, itemized)
              when the scenario freezes or changes the SD relative to projection year.
            - hi_personal_exemptions: Total personal exemption amount
            - hi_taxable_income: Taxable income after deductions/exemptions
            - hi_tax_before_credits: Tax from bracket calculation (CG cap applied)
            - hi_low_income_credit: Refundable food/excise tax credit (§235-55.85)
            - hi_tax_liability: Net Hawaii tax (may be negative if credit exceeds liability)
            - hi_cg_cap_savings: Tax reduction from CG cap (0 when cap not applied)
    """
    deduction_params = _normalize_deduction_params(
        deduction_params, tax_year, "calculate_hawaii_tax"
    )

    result = {
        'hi_agi': 0.0,
        'hi_standard_deduction': 0.0,
        'hi_itemized_deduction': 0.0,
        'hi_personal_exemptions': 0.0,
        'hi_taxable_income': 0.0,
        'hi_tax_before_credits': 0.0,
        'hi_low_income_credit': 0.0,
        'hi_tax_liability': 0.0,
        'hi_cg_cap_savings': 0.0,
    }

    filing_status = tax_unit.get('filing_status', 'single')
    agi = float(tax_unit.get('income', 0) or 0)
    num_dependents = int(tax_unit.get('num_dependents', 0))
    cg_income = max(0.0, float(cg_income))

    result['hi_agi'] = agi

    # Personal exemptions: taxpayer + spouse (if joint) + dependents
    # Exemption amount is year-specific (Act 46 raised it to $1,200 for TY2025+)
    exemption_per_unit = (
        _personal_exemption_for_year(tax_year)
        if tax_year >= 2024
        else params.personal_exemption_per_unit
    )
    num_exemptions = _count_exemptions(filing_status, num_dependents)
    exemption_amount = num_exemptions * exemption_per_unit
    result['hi_personal_exemptions'] = exemption_amount

    if tax_year >= 2024:
        # Route through hawaii_tax_real for year-specific brackets + deductions.
        from tax_modeler.projection.hawaii_tax_real import (  # noqa: PLC0415
            _deduction_for_year,
            hawaii_tax_real,
        )
        real_fs = _FS_REAL_MAP.get(filing_status, 'Single_Married_Separate')
        std_ded = _deduction_for_year(tax_year, real_fs)
        effective_ded, itemized_raw = _resolve_effective_deduction_pair(
            agi, filing_status, std_ded, deduction_params
        )
        result['hi_standard_deduction'] = effective_ded
        result['hi_itemized_deduction'] = itemized_raw
        taxable_income = max(0.0, agi - effective_ded - exemption_amount)
        result['hi_taxable_income'] = taxable_income
        income_for_real = agi - exemption_amount - (effective_ded - std_ded)
        tax_before_credits = hawaii_tax_real(income_for_real, tax_year, real_fs)

        # ---- HRS §235-51(f) capital gains cap -------------------------------
        if cg_income > 0:
            ordinary_agi = max(0.0, agi - cg_income)
            income_for_real_ord = ordinary_agi - exemption_amount - (effective_ded - std_ded)
            ordinary_tax = hawaii_tax_real(income_for_real_ord, tax_year, real_fs)
            cg_tax_uncapped = max(0.0, tax_before_credits - ordinary_tax)
            cg_tax_capped = min(cg_tax_uncapped, cg_income * HAWAII_CG_CAP_RATE)
            result['hi_cg_cap_savings'] = cg_tax_uncapped - cg_tax_capped
            tax_before_credits = ordinary_tax + cg_tax_capped
    else:
        std_ded = params.standard_deduction.get(filing_status, params.standard_deduction['single'])
        effective_ded, itemized_raw = _resolve_effective_deduction_pair(
            agi, filing_status, std_ded, deduction_params
        )
        result['hi_standard_deduction'] = effective_ded
        result['hi_itemized_deduction'] = itemized_raw
        taxable_income = max(0.0, agi - effective_ded - exemption_amount)
        result['hi_taxable_income'] = taxable_income
        brackets = params.brackets.get(filing_status, params.brackets['single'])
        tax_before_credits = _apply_brackets(taxable_income, brackets)

        # ---- HRS §235-51(f) capital gains cap -------------------------------
        if cg_income > 0:
            ordinary_taxable = max(0.0, (agi - cg_income) - effective_ded - exemption_amount)
            ordinary_tax = _apply_brackets(ordinary_taxable, brackets)
            cg_tax_uncapped = max(0.0, tax_before_credits - ordinary_tax)
            cg_tax_capped = min(cg_tax_uncapped, cg_income * HAWAII_CG_CAP_RATE)
            result['hi_cg_cap_savings'] = cg_tax_uncapped - cg_tax_capped
            tax_before_credits = ordinary_tax + cg_tax_capped

    result['hi_tax_before_credits'] = tax_before_credits

    # Refundable food/excise tax credit (HRS §235-55.85), statutory table for
    # the tax year: Act 163's for TY2023-2027, the prior table otherwise.
    from tax_modeler.credits.hi_food_excise import food_excise_credit  # noqa: PLC0415
    low_income_credit = food_excise_credit(agi, filing_status, num_exemptions, tax_year)
    result['hi_low_income_credit'] = low_income_credit

    # Net liability (can be negative = refund due to low-income credit)
    result['hi_tax_liability'] = tax_before_credits - low_income_credit

    return result


def _count_exemptions(filing_status: str, num_dependents: int) -> int:
    """
    Count the number of Hawaii personal exemptions for a tax unit.

    Exemptions:
    - 1 for the taxpayer
    - 1 for the spouse (joint filers only)
    - 1 per dependent
    """
    taxpayer_exemptions = 2 if filing_status == 'married_filing_jointly' else 1
    return taxpayer_exemptions + num_dependents


def statutory_exemption_counts(filing_status, num_dependents) -> np.ndarray:
    """Vectorized ``_count_exemptions``: filer, spouse on a joint return, and
    each dependent. A missing (NaN) dependent count counts as none."""
    status = np.asarray(filing_status)
    deps = np.nan_to_num(np.asarray(num_dependents, dtype=float), nan=0.0)
    return 1.0 + (status == "married_filing_jointly") + deps


def exemption_counts(tax_units: pd.DataFrame) -> np.ndarray:
    """Personal exemptions per tax unit, for the frame-level tax scorers.

    A ``num_exemptions`` column wins when the frame carries one (its NaNs are
    passed through, for callers that treat them as missing data). Otherwise
    each unit gets the statutory count ``calculate_hawaii_tax`` uses.

    The scorers used to default to one exemption per return when the column was
    absent, which it is on every projected frame: joint filers and filers with
    dependents were under-exempted, and a change to the exemption amount scored
    at about half its cost.
    """
    if "num_exemptions" in tax_units.columns:
        return tax_units["num_exemptions"].to_numpy(dtype=float)
    deps = (tax_units["num_dependents"].to_numpy(dtype=float)
            if "num_dependents" in tax_units.columns else np.zeros(len(tax_units)))
    return statutory_exemption_counts(tax_units["filing_status"].to_numpy(), deps)


def _apply_brackets(taxable_income: float, brackets: list[tuple[float, float]]) -> float:
    """
    Apply progressive tax brackets to taxable income.

    Args:
        taxable_income: Amount to tax
        brackets: List of (upper_bound, marginal_rate) tuples, ordered low to high.
                  The last tuple should have upper_bound = float('inf').

    Returns:
        Total tax owed
    """
    if taxable_income <= 0:
        return 0.0

    total_tax = 0.0
    prev_bound = 0.0

    for upper_bound, rate in brackets:
        if taxable_income <= prev_bound:
            break
        income_in_bracket = min(taxable_income, upper_bound) - prev_bound
        total_tax += income_in_bracket * rate
        prev_bound = upper_bound

    return round(total_tax, 2)


def calculate_hawaii_tax_for_units(
    tax_units_df: pd.DataFrame,
    tax_year: int = 2023,
    deduction_params: Any = None,
) -> pd.DataFrame:
    """
    Calculate Hawaii income tax for all tax units in a DataFrame.

    Adds columns: hi_agi, hi_standard_deduction, hi_itemized_deduction,
    hi_personal_exemptions, hi_taxable_income, hi_tax_before_credits,
    hi_low_income_credit, hi_tax_liability.

    Args:
        tax_units_df: DataFrame of tax units.
        tax_year: Tax year for bracket/deduction vintage (default 2023).
                  Pass ``tax_year=target_year`` from projection code so that
                  TY2025+ projections use Act 46 doubled thresholds.
        deduction_params: REQUIRED for tax_year ≥ 2024. Pass either
            scaled params from ``scale_deduction_params_for_target_year(year)``
            or ``NO_ITEMIZING`` to deliberately skip itemizing.

    Returns:
        DataFrame with Hawaii tax columns added.
    """
    normalized = _normalize_deduction_params(
        deduction_params, tax_year, "calculate_hawaii_tax_for_units"
    )
    return apply_columns(
        tax_units_df, lambda df: _hawaii_tax_columns(df, tax_year, normalized),
        _per_unit_fn(tax_units_df, tax_year, normalized))


def _calculate_hawaii_tax_per_unit(
    tax_units_df: pd.DataFrame, tax_year: int, normalized: dict | None,
) -> pd.DataFrame:
    """:func:`calculate_hawaii_tax` on each unit: the reference the array
    version (:func:`_hawaii_tax_columns`) reproduces, and its fallback."""
    return apply_per_unit(tax_units_df, _per_unit_fn(tax_units_df, tax_year, normalized))


def _per_unit_fn(tax_units_df: pd.DataFrame, tax_year: int, normalized: dict | None):
    """The per-unit call :func:`calculate_hawaii_tax_for_units` makes."""
    # Forward NO_ITEMIZING (not None) into the per-unit call so the inner
    # validation accepts it for tax_year ≥ 2024 without a redundant error.
    inner_params: Any = normalized if normalized is not None else NO_ITEMIZING

    # Derive per-unit capital gains income for the §235-51(f) cap.
    # synthetic_cg_share is set by UltraHighIncomeSynthesizerV2 for $1M+ rows;
    # base PUMS units don't have it, so they default to 0 (no cap applied).
    has_cg_share = "synthetic_cg_share" in tax_units_df.columns

    def one(unit: dict) -> dict[str, Any]:
        cg_income = 0.0
        if has_cg_share:
            cg_share = float(unit.get("synthetic_cg_share") or 0.0)
            income = float(unit.get("income", 0) or 0)
            cg_income = income * cg_share
        return calculate_hawaii_tax(
            unit, tax_year=tax_year, deduction_params=inner_params,
            cg_income=cg_income,
        )

    return one


# ---------------------------------------------------------------------------
# Array version of calculate_hawaii_tax_for_units
# ---------------------------------------------------------------------------
#
# The per-unit loop above was most of the time of every projection and every
# rescore of the calibrated base. The functions below compute the same columns
# for every unit at once with the same float operations in the same order, so
# each unit gets the same bits (tests/tax_modeler/test_vectorized_parity.py).
# Frames they do not handle (empty, repeated column names, missing or
# non-numeric inputs the scalar code converts or rejects one unit at a time, a
# pd.NA filing status) are handed to the per-unit path, which treats them as
# it always has.

def _hawaii_tax_columns(
    df: pd.DataFrame, tax_year: int, deduction_params: dict | None,
) -> dict[str, np.ndarray]:
    """:func:`calculate_hawaii_tax_for_units`'s columns for every unit at once
    (raises ``per_unit.NotVectorizable`` where the per-unit path is needed)."""
    agi = or_zero(numeric_column(df, "income", default=0.0))
    raw = numeric_column(df, "num_dependents", default=0.0)
    # int(NaN) raises per unit; past 2**31 the int64 exemption product could
    # overflow where the scalar's Python ints do not.
    if not (np.abs(raw) < 2.0 ** 31).all():
        raise NotVectorizable("num_dependents")
    deps = np.trunc(raw).astype(np.int64)
    cg_income = np.zeros(len(df))
    if "synthetic_cg_share" in df.columns:
        cg_income = py_max(0.0, agi * or_zero(numeric_column(df, "synthetic_cg_share")))
    return _hawaii_tax_arrays(agi, status_column(df), deps, cg_income, tax_year, deduction_params)


def _hawaii_tax_arrays(
    agi: np.ndarray,
    status: np.ndarray,
    num_dependents: np.ndarray,
    cg_income: np.ndarray,
    tax_year: int,
    deduction_params: dict | None,
) -> dict[str, np.ndarray]:
    """:func:`calculate_hawaii_tax` (with the default ``HAWAII_2023``
    parameters) over arrays: its result keys, in its order."""
    params = HAWAII_2023
    n = len(agi)
    num_exemptions = np.where(status == "married_filing_jointly", 2, 1) + num_dependents
    per_exemption = (_personal_exemption_for_year(tax_year) if tax_year >= 2024
                     else params.personal_exemption_per_unit)
    exemption_amount = num_exemptions * per_exemption
    itemized = np.zeros(n)
    savings = np.zeros(n)
    cg = cg_income > 0

    if tax_year >= 2024:
        from tax_modeler.projection.hawaii_tax_real import _deduction_for_year

        real = np.full(n, "Single_Married_Separate", dtype=object)
        for fs, real_fs in _FS_REAL_MAP.items():
            real[status == fs] = real_fs
        std = np.zeros(n)
        for real_fs in dict.fromkeys(_FS_REAL_MAP.values()):
            std[real == real_fs] = _deduction_for_year(tax_year, real_fs)
        effective = std
        if deduction_params is not None:
            itemized = _expected_itemized_arrays(agi, status, deduction_params)
            effective = py_max(std, itemized)
        taxable = py_max(0.0, agi - effective - exemption_amount)
        tax_before = _hawaii_tax_real_arrays(
            agi - exemption_amount - (effective - std), tax_year, real)
        if cg.any():
            ordinary_agi = py_max(0.0, agi[cg] - cg_income[cg])
            ordinary_tax = _hawaii_tax_real_arrays(
                ordinary_agi - exemption_amount[cg] - (effective[cg] - std[cg]), tax_year, real[cg])
            uncapped = py_max(0.0, tax_before[cg] - ordinary_tax)
            capped = py_min(uncapped, cg_income[cg] * HAWAII_CG_CAP_RATE)
            savings[cg] = uncapped - capped
            tax_before[cg] = ordinary_tax + capped
    else:
        std = np.full(n, params.standard_deduction['single'])
        bracket_of = np.zeros(n, dtype=np.int64)       # index into ``schedules``
        schedules = [params.brackets['single']]
        for fs, amount in params.standard_deduction.items():
            std[status == fs] = amount
        for fs, brackets in params.brackets.items():
            schedules.append(brackets)
            bracket_of[status == fs] = len(schedules) - 1
        effective = std
        if deduction_params is not None:
            itemized = _expected_itemized_arrays(agi, status, deduction_params)
            effective = py_max(std, itemized)
        taxable = py_max(0.0, agi - effective - exemption_amount)
        tax_before = np.zeros(n)
        ordinary_taxable = py_max(0.0, agi - cg_income - effective - exemption_amount)
        ordinary_tax = np.zeros(n)
        for k, brackets in enumerate(schedules):
            m = bracket_of == k
            if m.any():
                tax_before[m] = _apply_brackets_arrays(taxable[m], brackets)
                if (m & cg).any():
                    ordinary_tax[m & cg] = _apply_brackets_arrays(ordinary_taxable[m & cg], brackets)
        if cg.any():
            uncapped = py_max(0.0, tax_before[cg] - ordinary_tax[cg])
            capped = py_min(uncapped, cg_income[cg] * HAWAII_CG_CAP_RATE)
            savings[cg] = uncapped - capped
            tax_before[cg] = ordinary_tax[cg] + capped

    credit = _food_excise_arrays(agi, status, num_exemptions, tax_year)
    return {
        'hi_agi': agi,
        'hi_standard_deduction': effective,
        'hi_itemized_deduction': itemized,
        'hi_personal_exemptions': exemption_amount,
        'hi_taxable_income': taxable,
        'hi_tax_before_credits': tax_before,
        'hi_low_income_credit': credit,
        'hi_tax_liability': tax_before - credit,
        'hi_cg_cap_savings': savings,
    }


def _expected_itemized_arrays(agi: np.ndarray, status: np.ndarray, deduction_params: dict) -> np.ndarray:
    """:func:`_expected_itemized_deductions` for every unit. Like the scalar,
    it reads only the homeownership configs of the statuses present and only
    the tiers some unit reaches, so caller-supplied params missing the rest
    still work."""
    joint = (status == "married_filing_jointly") | (status == "qualifying_widow")
    hoh = ~joint & (status == "head_of_household")
    ho_cfg = deduction_params["homeownership"]
    ho = {key: np.zeros(len(agi)) for key in ("cap", "base", "income_scale", "slope")}
    for name, m in (("joint", joint), ("hoh", hoh), ("other", ~joint & ~hoh)):
        if m.any():
            for key in ho:
                ho[key][m] = ho_cfg[name][key]

    p_own = py_min(ho["cap"], ho["base"] + (agi / ho["income_scale"]) * ho["slope"])

    mort = deduction_params["mortgage_interest_tiers"]
    base_interest = _first_tier(agi, mort["tiers"], "base_interest", mort["default_base_interest"])
    base_interest = np.where(joint, base_interest * mort.get("joint_multiplier", 1.0), base_interest)
    expected_mortgage = p_own * base_interest

    charit = deduction_params["charitable_giving"]
    giving_rate = _first_tier(agi, charit["rates_by_agi"], "rate", charit["default_rate"])
    expected_charitable = charit["claim_rate"] * agi * giving_rate

    med = deduction_params["medical_expenses"]
    threshold = agi * med["agi_threshold"]
    expected_medical = med["claim_rate_other"] * py_max(0.0, agi * med["expense_pct_other"] - threshold)

    re = deduction_params["real_estate_taxes"]
    re_amount = _first_tier(agi, re["tiers"], "amount", re["default_amount"])

    total = expected_mortgage + expected_charitable + expected_medical + re_amount

    pease_threshold = np.where(status == "married_filing_separately", 83_400, 166_800)
    reduction = 0.03 * (agi - pease_threshold)
    total = np.where(agi > pease_threshold, total - py_min(reduction, 0.80 * total), total)
    return py_max(0.0, total)


def _first_tier(agi: np.ndarray, tiers: list, key: str, default) -> np.ndarray:
    """The ``for tier in tiers: if agi < tier["max_agi"]: ...; break`` lookups:
    the first matching tier's ``key``, else ``default``. A tier is read only
    when some unit reaches it, as in the scalar loop."""
    out = np.full(len(agi), default, dtype=float)
    todo = np.ones(len(agi), dtype=bool)
    for tier in tiers:
        if not todo.any():
            break
        hit = todo & (agi < tier["max_agi"])
        if hit.any():
            out[hit] = tier[key]
        todo &= ~hit
    return out


def _apply_brackets_arrays(taxable_income: np.ndarray, brackets: list[tuple[float, float]]) -> np.ndarray:
    """:func:`_apply_brackets` for every unit: the same running sum, bracket
    by bracket, and the same ``round(total, 2)``."""
    total = np.zeros(len(taxable_income))
    taxed = ~(taxable_income <= 0)
    active = taxed.copy()
    prev_bound = 0.0
    for upper_bound, rate in brackets:
        active &= ~(taxable_income <= prev_bound)
        if not active.any():
            break
        in_bracket = py_min(taxable_income[active], upper_bound) - prev_bound
        total[active] = total[active] + in_bracket * rate
        prev_bound = upper_bound
    out = np.zeros(len(taxable_income))
    out[taxed] = py_round(total[taxed], 2)
    return out


def _hawaii_tax_real_arrays(income: np.ndarray, tax_year: int, real_fs: np.ndarray) -> np.ndarray:
    """``hawaii_tax_real(income, tax_year, real_fs)`` for every unit."""
    from tax_modeler.projection.hawaii_tax_real import (
        _bracket_year,
        _deduction_for_year,
        _load_brackets,
    )

    out = np.zeros(len(income))
    live = ~(income <= 0)
    byr = _bracket_year(tax_year)
    for fs in dict.fromkeys(real_fs.tolist()):
        m = live & (real_fs == fs)
        if not m.any():
            continue
        brackets = _load_brackets().get((byr, fs))
        if not brackets:
            import logging
            logging.getLogger("tax_modeler.projection.hawaii_tax_real").warning(
                "No bracket schedule for (year=%d, filing_status=%s); returning 0", byr, fs)
            continue
        out[m] = _apply_schedule_arrays(income[m], brackets, _deduction_for_year(tax_year, fs))
    return out


def _apply_schedule_arrays(income: np.ndarray, brackets: list, deduction: float) -> np.ndarray:
    """``hawaii_tax_real._apply_schedule`` for every unit, including its
    top-bracket fallback (a unit below a bracket's floor, or past the last)."""
    taxable = py_max(income - deduction, 0.0)
    out = np.zeros(len(income))
    active = ~(taxable <= 0.0)
    top = np.zeros(len(income), dtype=bool)
    for lo, hi, rate, base_tax, base_income in brackets:
        below = active & (taxable <= lo)
        top |= below
        active &= ~below
        hit = active & (taxable <= hi)
        out[hit] = base_tax + (taxable[hit] - base_income) * rate
        active &= ~hit
    top |= active
    lo, hi, rate, base_tax, base_income = brackets[-1]
    out[top] = base_tax + (taxable[top] - base_income) * rate
    return out


def _food_excise_arrays(agi: np.ndarray, status: np.ndarray, num_exemptions: np.ndarray,
                        tax_year: int) -> np.ndarray:
    """``food_excise_credit(agi, status, num_exemptions, tax_year)`` for every
    unit, through the vectorized table lookup the scalar is tested against
    (``hi_food_excise._per_exemption``)."""
    from tax_modeler.credits.hi_food_excise import (
        _JOINT_TABLE_STATUSES,
        _per_exemption,
        hawaii_food_excise_parameters,
    )

    p = hawaii_food_excise_parameters(tax_year)
    joint_table = np.zeros(len(agi), dtype=bool)
    for s in _JOINT_TABLE_STATUSES:
        joint_table |= status == s
    x = py_max(agi, 0.0)
    f = p.income_threshold_factor
    per_ex = np.where(joint_table, _per_exemption(x, p.joint, f), _per_exemption(x, p.single, f))
    return per_ex * py_max(num_exemptions, 0) * p.amount_pct
