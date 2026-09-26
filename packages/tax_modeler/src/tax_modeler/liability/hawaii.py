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
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd


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
    _instance: Optional["_NoItemizingSentinel"] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "NO_ITEMIZING"


NO_ITEMIZING = _NoItemizingSentinel()


def _normalize_deduction_params(
    deduction_params: Any, tax_year: int, caller: str,
) -> Optional[dict]:
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
    standard_deduction: Dict[str, float] = field(default_factory=lambda: {
        'single':                    2_200,
        'married_filing_jointly':    4_400,
        'married_filing_separately': 2_200,
        'head_of_household':         3_212,
    })

    # Personal exemption per exemption claimed (taxpayer + spouse + each dependent)
    personal_exemption_per_unit: float = 1_144

    # Tax brackets: list of (upper_bound, marginal_rate) pairs, from lowest to highest.
    # The last bracket has upper_bound = infinity.
    # Brackets are keyed by filing status.
    brackets: Dict[str, List[Tuple[float, float]]] = field(default_factory=lambda: {
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

# HRS §235-16: net long-term capital gains taxed at no more than 7.25%.
# SB 3125 CD1 amends §235-51 (ordinary income brackets) only; the CG cap
# is unchanged and applies under both Act 46 and SB 3125 CD1 baselines.
HAWAII_CG_CAP_RATE = 0.0725

# ---------------------------------------------------------------------------
# Year-specific schedules (Act 46 phase-in)
# ---------------------------------------------------------------------------

# Personal exemption per unit, step-function by effective tax year.
# Matches TaxSystemRegistry.PERSONAL_EXEMPTIONS in config/tax_system_config.py.
_PERSONAL_EXEMPTION_SCHEDULE: List[Tuple[int, float]] = [
    (2018, 1_144.0),  # pre-Act-46 (applies for TY≤2024)
    (2025, 1_200.0),  # Act 46 phase-in (TY2025+)
]

# Maps liability module filing-status strings → hawaii_tax_real module strings.
_FS_REAL_MAP: Dict[str, str] = {
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
    deduction_params: Optional[dict],
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
    deduction_params: Optional[dict],
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
    tax_unit: Dict,
    params: HawaiiTaxParameters = HAWAII_2023,
    tax_year: int = 2023,
    deduction_params: Any = None,
    cg_income: float = 0.0,
) -> Dict[str, float]:
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
            When non-zero, the HRS §235-16 cap is applied: the incremental
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

        # ---- HRS §235-16 capital gains cap ----------------------------------
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

        # ---- HRS §235-16 capital gains cap ----------------------------------
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


def _apply_brackets(taxable_income: float, brackets: List[Tuple[float, float]]) -> float:
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
    # Forward NO_ITEMIZING (not None) into the per-unit call so the inner
    # validation accepts it for tax_year ≥ 2024 without a redundant error.
    inner_params: Any = normalized if normalized is not None else NO_ITEMIZING

    # Derive per-unit capital gains income for the §235-16 cap.
    # synthetic_cg_share is set by UltraHighIncomeSynthesizerV2 for $1M+ rows;
    # base PUMS units don't have it, so they default to 0 (no cap applied).
    has_cg_share = "synthetic_cg_share" in tax_units_df.columns
    results = []
    for _, row in tax_units_df.iterrows():
        unit = row.to_dict()
        cg_income = 0.0
        if has_cg_share:
            cg_share = float(row.get("synthetic_cg_share") or 0.0)
            income = float(row.get("income", 0) or 0)
            cg_income = income * cg_share
        hi_tax = calculate_hawaii_tax(
            unit, tax_year=tax_year, deduction_params=inner_params,
            cg_income=cg_income,
        )
        unit.update(hi_tax)
        results.append(unit)
    return pd.DataFrame(results)
