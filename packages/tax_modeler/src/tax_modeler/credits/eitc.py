"""
Earned Income Tax Credit (EITC) Calculation Module

Implements the federal EITC with year-specific statutory parameters.
The EITC is a refundable credit for low-to-moderate income workers.

Supported tax years: 2022-2026.

Sources (IRS Revenue Procedures, inflation-adjusted tables):
- TY 2022: IRS Rev. Proc. 2021-45
- TY 2023: IRS Rev. Proc. 2022-38
- TY 2024: IRS Rev. Proc. 2023-34
- TY 2025: IRS Rev. Proc. 2024-40
- TY 2026: IRS Rev. Proc. 2025-32

Key rules:
- Must have earned income (wages or self-employment)
- Investment income must not exceed the year-specific limit
- Married Filing Separately filers are ineligible
- Credit amount depends on filing status, number of qualifying children,
  and earned income / AGI (the lesser determines the credit)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from tax_modeler.units.relshipp_codes import EITC_QUALIFYING_CHILD_RELS


@dataclass
class EITCChildParameters:
    """EITC parameters for a specific number of qualifying children."""
    max_credit: float
    phase_in_rate: float          # Rate applied to earned income in phase-in region
    phase_in_ends: float          # Earned income where max credit is first reached
    phaseout_start_single: float  # AGI/EI where phaseout begins (single/HoH/MFS)
    phaseout_start_joint: float   # AGI/EI where phaseout begins (MFJ)
    phaseout_rate: float          # Rate at which credit is reduced in phaseout region


@dataclass
class EITCParameters:
    """EITC parameters for a single tax year.

    Use ``eitc_parameters_for_year(year)`` to build the parameters for a
    supported tax year. The no-argument constructor returns the TY 2023
    defaults to preserve historical call sites.
    """

    investment_income_limit: float = 11_000  # TY 2023 default

    by_children: Dict[int, EITCChildParameters] = field(default_factory=lambda: {
        0: EITCChildParameters(
            max_credit=600,
            phase_in_rate=0.0765,
            phase_in_ends=7_840,
            phaseout_start_single=9_800,
            phaseout_start_joint=17_640,
            phaseout_rate=0.0765,
        ),
        1: EITCChildParameters(
            max_credit=3_995,
            phase_in_rate=0.34,
            phase_in_ends=11_750,
            phaseout_start_single=21_560,
            phaseout_start_joint=28_120,
            phaseout_rate=0.1598,
        ),
        2: EITCChildParameters(
            max_credit=6_604,
            phase_in_rate=0.40,
            phase_in_ends=16_510,
            phaseout_start_single=21_560,
            phaseout_start_joint=28_120,
            phaseout_rate=0.2106,
        ),
        3: EITCChildParameters(  # 3 or more children
            max_credit=7_430,
            phase_in_rate=0.45,
            phase_in_ends=16_510,
            phaseout_start_single=21_560,
            phaseout_start_joint=28_120,
            phaseout_rate=0.2106,
        ),
    })


# ---------------------------------------------------------------------------
# Year-keyed parameter factories
# ---------------------------------------------------------------------------

# Phase-in / phase-out rates are statutory (IRC §32) and identical across years.
# Only the max credit, phase-in completion income, phaseout start income, and
# investment-income limit are inflation-adjusted (per the cited Rev. Procs.).

_EITC_PHASE_IN_RATES = {0: 0.0765, 1: 0.34, 2: 0.40, 3: 0.45}
_EITC_PHASEOUT_RATES = {0: 0.0765, 1: 0.1598, 2: 0.2106, 3: 0.2106}


def _build_eitc_params(
    *,
    investment_income_limit: float,
    max_credits: Dict[int, float],
    phase_in_ends: Dict[int, float],
    phaseout_start_single: Dict[int, float],
    phaseout_start_joint: Dict[int, float],
) -> EITCParameters:
    by_children = {
        n: EITCChildParameters(
            max_credit=max_credits[n],
            phase_in_rate=_EITC_PHASE_IN_RATES[n],
            phase_in_ends=phase_in_ends[n],
            phaseout_start_single=phaseout_start_single[n],
            phaseout_start_joint=phaseout_start_joint[n],
            phaseout_rate=_EITC_PHASEOUT_RATES[n],
        )
        for n in (0, 1, 2, 3)
    }
    p = EITCParameters(investment_income_limit=investment_income_limit)
    p.by_children = by_children
    return p


def _eitc_2022() -> EITCParameters:
    # IRS Rev. Proc. 2021-45 (TY 2022). Investment income limit $10,300.
    return _build_eitc_params(
        investment_income_limit=10_300,
        max_credits={0: 560, 1: 3_733, 2: 6_164, 3: 6_935},
        phase_in_ends={0: 7_320, 1: 10_980, 2: 15_410, 3: 15_410},
        phaseout_start_single={0: 9_160, 1: 20_130, 2: 20_130, 3: 20_130},
        phaseout_start_joint={0: 15_290, 1: 26_260, 2: 26_260, 3: 26_260},
    )


def _eitc_2023() -> EITCParameters:
    # IRS Rev. Proc. 2022-38 (TY 2023). Investment income limit $11,000.
    return _build_eitc_params(
        investment_income_limit=11_000,
        max_credits={0: 600, 1: 3_995, 2: 6_604, 3: 7_430},
        phase_in_ends={0: 7_840, 1: 11_750, 2: 16_510, 3: 16_510},
        phaseout_start_single={0: 9_800, 1: 21_560, 2: 21_560, 3: 21_560},
        phaseout_start_joint={0: 17_640, 1: 28_120, 2: 28_120, 3: 28_120},
    )


def _eitc_2024() -> EITCParameters:
    # IRS Rev. Proc. 2023-34 (TY 2024). Investment income limit $11,600.
    return _build_eitc_params(
        investment_income_limit=11_600,
        max_credits={0: 632, 1: 4_213, 2: 6_960, 3: 7_830},
        phase_in_ends={0: 8_260, 1: 12_390, 2: 17_400, 3: 17_400},
        phaseout_start_single={0: 10_330, 1: 22_720, 2: 22_720, 3: 22_720},
        phaseout_start_joint={0: 17_250, 1: 29_640, 2: 29_640, 3: 29_640},
    )


def _eitc_2025() -> EITCParameters:
    # IRS Rev. Proc. 2024-40 (TY 2025). Investment income limit $11,950.
    return _build_eitc_params(
        investment_income_limit=11_950,
        max_credits={0: 649, 1: 4_328, 2: 7_152, 3: 8_046},
        phase_in_ends={0: 8_490, 1: 12_730, 2: 17_880, 3: 17_880},
        phaseout_start_single={0: 10_620, 1: 23_350, 2: 23_350, 3: 23_350},
        phaseout_start_joint={0: 17_730, 1: 30_470, 2: 30_470, 3: 30_470},
    )


def _eitc_2026() -> EITCParameters:
    # IRS Rev. Proc. 2025-32 §4.06 (TY 2026). Investment income limit $12,200.
    return _build_eitc_params(
        investment_income_limit=12_200,
        max_credits={0: 664, 1: 4_427, 2: 7_316, 3: 8_231},
        phase_in_ends={0: 8_680, 1: 13_020, 2: 18_290, 3: 18_290},
        phaseout_start_single={0: 10_860, 1: 23_890, 2: 23_890, 3: 23_890},
        phaseout_start_joint={0: 18_140, 1: 31_160, 2: 31_160, 3: 31_160},
    )


_EITC_PARAMS_BY_YEAR = {
    2022: _eitc_2022,
    2023: _eitc_2023,
    2024: _eitc_2024,
    2025: _eitc_2025,
    2026: _eitc_2026,
}

# Assumed chained-CPI growth for extrapolating IRS Rev. Proc. parameters past
# the last published year (CBO Jan-2025 long-run chained-CPI projection).
# IRC §32(j) indexes EITC dollar amounts to chained CPI with rounding rules:
# credit/income amounts round to the nearest $10; the investment-income limit
# rounds to the nearest $50 (IRC §32(j)(2)).
CREDIT_PARAM_CPI_GROWTH = 0.021


def _first_present(tax_unit: Dict, *keys: str) -> float:
    for key in keys:
        val = tax_unit.get(key)
        if val is not None and not pd.isna(val):
            return float(val)
    return 0.0


def _round10(x: float) -> float:
    return float(round(x / 10.0) * 10)


def _round50(x: float) -> float:
    return float(round(x / 50.0) * 50)


def _eitc_extrapolated(tax_year: int) -> EITCParameters:
    """Extrapolate EITC dollar parameters past the last published Rev. Proc.

    Scales the latest published year's dollar amounts by an assumed
    chained-CPI factor (``CREDIT_PARAM_CPI_GROWTH``/yr, compounded) and applies
    the statutory rounding conventions. Phase-in/phase-out RATES are statutory
    (IRC §32(b)) and are not scaled.
    """
    base_year = max(_EITC_PARAMS_BY_YEAR)
    base = _EITC_PARAMS_BY_YEAR[base_year]()
    factor = (1.0 + CREDIT_PARAM_CPI_GROWTH) ** (tax_year - base_year)
    return _build_eitc_params(
        investment_income_limit=_round50(base.investment_income_limit * factor),
        max_credits={n: _round10(p.max_credit * factor) for n, p in base.by_children.items()},
        phase_in_ends={n: _round10(p.phase_in_ends * factor) for n, p in base.by_children.items()},
        phaseout_start_single={n: _round10(p.phaseout_start_single * factor) for n, p in base.by_children.items()},
        phaseout_start_joint={n: _round10(p.phaseout_start_joint * factor) for n, p in base.by_children.items()},
    )


def eitc_parameters_for_year(tax_year: int, *, extrapolate: bool = False) -> EITCParameters:
    """Return EITC parameters for the requested tax year.

    With ``extrapolate=True``, years beyond the last published Rev. Proc. are
    CPI-extrapolated (see :func:`_eitc_extrapolated`) instead of raising —
    used by the forward projector so credits keep pace with projected nominal
    income growth (otherwise they phase out too early in real terms).

    Raises ``KeyError`` for unsupported years when not extrapolating (or for
    years before the earliest published year regardless).
    """
    if tax_year in _EITC_PARAMS_BY_YEAR:
        return _EITC_PARAMS_BY_YEAR[tax_year]()
    if extrapolate and tax_year > max(_EITC_PARAMS_BY_YEAR):
        return _eitc_extrapolated(tax_year)
    raise KeyError(
        f"EITC parameters not defined for tax year {tax_year}. "
        f"Supported years: {sorted(_EITC_PARAMS_BY_YEAR)}"
        + ("" if extrapolate else " (pass extrapolate=True for later years)")
    )


def classify_eitc_region(
    earned_income,
    num_qualifying_children,
    filing_status,
    *,
    tax_year: int,
) -> np.ndarray:
    """Classify each row's federal-EITC schedule region: phase_in / plateau / phase_out.

    Vectorized over array-like inputs. Boundary convention: earnings
    exactly equal to ``phase_in_ends`` or ``phaseout_start`` map to
    ``"plateau"``; strict inequality is required to fall into
    ``"phase_in"`` or ``"phase_out"``. Used by the intensive-margin
    behavioral response in ``scenarios/eitc_labor_response.py`` to apply
    a piecewise hours elasticity (phase-in workers respond most
    strongly to EITC subsidy changes).

    Parameters
    ----------
    earned_income, num_qualifying_children, filing_status
        Array-like, all the same length. ``num_qualifying_children`` is
        clamped to [0, 3] to match the EITC parameter schedule (3 = "3
        or more"). ``filing_status`` strings are compared against
        ``"married_filing_jointly"`` for the joint-vs-single phaseout
        boundary.
    tax_year
        Used to look up the EITC parameter set; must be a supported
        year per :func:`eitc_parameters_for_year`.

    Returns
    -------
    np.ndarray of object dtype, values in {"phase_in", "plateau", "phase_out"}.
    """
    params = eitc_parameters_for_year(tax_year)
    ei = np.asarray(earned_income, dtype=float)
    n_kids = np.clip(np.asarray(num_qualifying_children, dtype=int), 0, 3)
    is_joint = np.asarray(filing_status) == "married_filing_jointly"

    phase_in_ends = np.array(
        [params.by_children[k].phase_in_ends for k in n_kids], dtype=float
    )
    phaseout_start_single = np.array(
        [params.by_children[k].phaseout_start_single for k in n_kids], dtype=float
    )
    phaseout_start_joint = np.array(
        [params.by_children[k].phaseout_start_joint for k in n_kids], dtype=float
    )
    phaseout_start = np.where(is_joint, phaseout_start_joint, phaseout_start_single)

    region = np.full(len(ei), "plateau", dtype=object)
    region[ei < phase_in_ends] = "phase_in"
    region[ei > phaseout_start] = "phase_out"
    return region


def calculate_eitc(
    tax_unit: Dict,
    tax_year: int = 2023,
    *,
    extrapolate: bool = False,
) -> Dict[str, float]:
    """
    Calculate the EITC for a single tax unit.

    Args:
        tax_unit: Dictionary with keys:
            - filing_status: str
            - income: float  (AGI proxy — PINCP * ADJINC)
            - earned_income: float  (WAGP + SEMP, ADJINC-adjusted)
            - investment_income: float  (INTP, ADJINC-adjusted)
            - dependents_details: list of dicts (age, relationship, citizenship)
            - num_dependents: int
        tax_year: Tax year (default 2023)
        extrapolate: CPI-extrapolate parameters for years beyond the last
            published Rev. Proc. instead of raising KeyError.

    Returns:
        Dictionary with:
            - eitc_amount: Total EITC credit
            - eitc_qualifying_children: Number of qualifying children for EITC
            - eitc_eligible: Whether the unit is eligible for EITC at all
    """
    params = eitc_parameters_for_year(tax_year, extrapolate=extrapolate)

    result = {
        'eitc_amount': 0.0,
        'eitc_qualifying_children': 0,
        'eitc_eligible': False,
    }

    filing_status = tax_unit.get('filing_status', 'single')

    # MFS filers are categorically ineligible
    if filing_status == 'married_filing_separately':
        return result

    # Federal columns (W-2 wages, §86 taxable SS) when present; see
    # liability.federal.add_federal_income_columns.
    earned_income = _first_present(tax_unit, 'federal_earned_income', 'earned_income')
    investment_income = float(tax_unit.get('investment_income', 0) or 0)
    agi = _first_present(tax_unit, 'federal_agi', 'income')

    # Must have positive earned income
    if earned_income <= 0:
        return result

    # Investment income test — exceeding the limit disqualifies the whole unit
    if investment_income > params.investment_income_limit:
        return result

    # Count qualifying children for EITC (age < 19, or < 24 if student, or disabled)
    dependents = tax_unit.get('dependents_details') or []
    num_qualifying = _count_qualifying_children_eitc(dependents)
    result['eitc_qualifying_children'] = num_qualifying

    # IRC §32(c)(1)(A)(ii): childless filers must be aged 25-64
    if num_qualifying == 0:
        filer_age = int(tax_unit.get('primary_agep') or 40)
        if not (25 <= filer_age <= 64):
            return result

    # Clamp to the highest-bracket key (3 = "3 or more")
    bracket_key = min(num_qualifying, 3)
    child_params = params.by_children[bracket_key]

    is_joint = filing_status == 'married_filing_jointly'
    phaseout_start = child_params.phaseout_start_joint if is_joint else child_params.phaseout_start_single

    # EITC is the lesser of:
    #   (a) credit based on earned income alone
    #   (b) credit based on AGI (for phaseout purposes, IRS uses the higher of EI or AGI)
    # The IRS rule: use earned income to determine phase-in, and the GREATER of
    # earned income or AGI for the phaseout comparison.
    phaseout_income = max(earned_income, agi)

    credit = _compute_credit(earned_income, phaseout_income, phaseout_start, child_params)

    if credit > 0:
        result['eitc_eligible'] = True
        result['eitc_amount'] = round(credit, 2)

    return result


def _count_qualifying_children_eitc(dependents: List[Dict]) -> int:
    """
    Count dependents who qualify as EITC qualifying children.

    EITC qualifying child rules (differ from CTC):
    - Age: under 19; or under 24 if a full-time student (SCHL >= 16); or any age if disabled (DIS=1)
    - Relationship: own child (bio 25 / adopted 26 / step 27), sibling (28),
                    grandchild (30), foster child (35) -- see
                    ``relshipp_codes.EITC_QUALIFYING_CHILD_RELS``
    - Must be a US citizen/national/resident alien (CIT 1-4)
    - Cannot be married filing jointly (simplified: check MAR != 1)

    Note: EITC does NOT have the under-17 age cap that CTC uses.
    """
    QUALIFYING_RELATIONSHIPS = EITC_QUALIFYING_CHILD_RELS
    count = 0

    for dep in dependents:
        age = int(dep.get('age', 0))
        relationship = dep.get('relationship', 0)
        citizenship = dep.get('citizenship', 1)
        school_level = int(dep.get('school_level', 0))  # SCHL field if stored
        disabled = dep.get('disabled', False)           # DIS == 1 if stored

        # Relationship test
        try:
            rel_int = int(relationship)
        except (ValueError, TypeError):
            continue
        if rel_int not in QUALIFYING_RELATIONSHIPS:
            continue

        # Age test
        age_qualifies = (
            age < 19 or
            (age < 24 and school_level >= 16) or
            disabled
        )
        if not age_qualifies:
            continue

        # Citizenship test (CIT 1-4 = US citizen or national; 5 = not a citizen)
        try:
            cit = int(citizenship)
        except (ValueError, TypeError):
            cit = 1
        if cit > 4:
            continue

        count += 1

    return count


def _compute_credit(
    earned_income: float,
    phaseout_income: float,
    phaseout_start: float,
    params: EITCChildParameters,
) -> float:
    """
    Compute the EITC amount given earned income, phaseout income, and parameters.

    Three regions:
    1. Phase-in:   EI < phase_in_ends       → credit = EI * phase_in_rate
    2. Flat:       phase_in_ends ≤ EI ≤ phaseout_start  → credit = max_credit
    3. Phase-out:  EI > phaseout_start      → credit = max_credit - excess * phaseout_rate
    """
    # Phase-in credit based on earned income
    phase_in_credit = min(earned_income * params.phase_in_rate, params.max_credit)

    # Phase-out reduction based on phaseout_income (greater of EI or AGI)
    if phaseout_income <= phaseout_start:
        reduction = 0.0
    else:
        excess = phaseout_income - phaseout_start
        reduction = excess * params.phaseout_rate

    credit = max(0.0, phase_in_credit - reduction)
    return credit


def calculate_eitc_for_tax_units(
    tax_units_df: pd.DataFrame,
    tax_year: int = 2023,
    *,
    extrapolate: bool = False,
) -> pd.DataFrame:
    """
    Calculate EITC for all tax units in a DataFrame.

    Adds columns: eitc_amount, eitc_qualifying_children, eitc_eligible.

    Args:
        tax_units_df: DataFrame of tax units (must include income, earned_income,
                      investment_income, filing_status, dependents_details).
        tax_year: Tax year for parameter selection (default 2023).
        extrapolate: CPI-extrapolate parameters for years beyond the last
            published Rev. Proc. instead of raising KeyError.

    Returns:
        DataFrame with EITC columns added.
    """
    results = []
    for _, row in tax_units_df.iterrows():
        unit = row.to_dict()
        eitc = calculate_eitc(unit, tax_year=tax_year, extrapolate=extrapolate)
        unit.update(eitc)
        results.append(unit)
    return pd.DataFrame(results)


def get_eitc_summary_stats(tax_units_df: pd.DataFrame) -> Dict[str, float]:
    """Summary statistics for EITC across tax units (weighted by 'weight' column)."""
    if 'eitc_amount' not in tax_units_df.columns:
        tax_units_df = calculate_eitc_for_tax_units(tax_units_df)

    weight = tax_units_df.get('weight', pd.Series(1.0, index=tax_units_df.index))
    eligible = tax_units_df['eitc_eligible']

    return {
        'total_tax_units': int((weight).sum()),
        'units_receiving_eitc': int((weight[eligible]).sum()),
        'total_eitc_amount': float((tax_units_df['eitc_amount'] * weight).sum()),
        'average_eitc_per_recipient': float(
            (tax_units_df.loc[eligible, 'eitc_amount'] * weight[eligible]).sum()
            / weight[eligible].sum()
        ) if weight[eligible].sum() > 0 else 0.0,
        'total_qualifying_children': int(
            (tax_units_df['eitc_qualifying_children'] * weight).sum()
        ),
    }
