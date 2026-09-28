"""
Child Tax Credit (CTC) Calculation Module

Implements the federal Child Tax Credit with year-specific statutory
parameters: $2,000 per qualifying child under TCJA through TY 2024, raised
to $2,200 for TY 2025 and indexed thereafter by the 2025 reconciliation act
(P.L. 119-21 §70104), with an inflation-indexed refundable portion (ACTC).

Supported tax years: 2022-2026 (later years with ``extrapolate=True``).

Sources:
- TY 2022: IRS Rev. Proc. 2021-45  → $2,000 max, refundable cap $1,500/child
- TY 2023: IRS Rev. Proc. 2022-38  → $2,000 max, refundable cap $1,600/child
- TY 2024: IRS Rev. Proc. 2023-34  → $2,000 max, refundable cap $1,700/child
- TY 2025: P.L. 119-21 §70104 ($2,200 max); Rev. Proc. 2024-40 ($1,700 cap)
- TY 2026: IRS Rev. Proc. 2025-32 §4.05 → $2,200 max, refundable cap $1,700

Phaseout thresholds ($200K single / $400K joint) are statutory and not
indexed. From TY 2025 the taxpayer (or, on a joint return, at least one
spouse) must have a work-eligible SSN (§24(h)(7) as amended).

Ordering (IRC §24(b)(3), §24(d)): the credit first offsets federal income
tax before credits; only the unused remainder can be refunded, and the
refund is limited to 15% of earned income above $2,500 (a per-return
amount, not per child) and to the per-child cap.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Tuple
import pandas as pd
import numpy as np
from dataclasses import dataclass


@dataclass
class CTCParameters:
    """Parameters for CTC calculation.

    Defaults reflect TY 2023 to preserve historical call sites. Use
    ``ctc_parameters_for_year(year)`` to build a year-specific instance.
    """
    max_credit_per_child: int = 2000
    refundable_limit_per_child: int = 1600  # Additional Child Tax Credit (ACTC), TY 2023
    phaseout_threshold_single: int = 200000
    phaseout_threshold_joint: int = 400000
    phaseout_rate: float = 0.05  # $50 per $1,000 over threshold
    qualifying_age_limit: int = 17  # Must be under 17
    earned_income_threshold: int = 2500  # ACTC earned-income threshold (TCJA, IRC §24(d))
    actc_earned_income_rate: float = 0.15  # ACTC = 15% of earned income above threshold
    require_filer_ssn: bool = False  # P.L. 119-21: taxpayer/spouse SSN, TY 2025+


# Maximum credit per child. TCJA's $2,000 was not indexed; P.L. 119-21
# §70104 set $2,200 for TY 2025 and indexed it from TY 2026.
_CTC_MAX_CREDIT_BY_YEAR = {
    2022: 2_000,
    2023: 2_000,
    2024: 2_000,
    2025: 2_200,  # P.L. 119-21 §70104
    2026: 2_200,  # IRS Rev. Proc. 2025-32 §4.05(1)
}

# Refundable per-child cap (Additional CTC) is inflation-indexed under TCJA.
_CTC_REFUNDABLE_LIMIT_BY_YEAR = {
    2022: 1_500,  # IRS Rev. Proc. 2021-45
    2023: 1_600,  # IRS Rev. Proc. 2022-38
    2024: 1_700,  # IRS Rev. Proc. 2023-34
    2025: 1_700,  # IRS Rev. Proc. 2024-40
    2026: 1_700,  # IRS Rev. Proc. 2025-32 §4.05(2)
}

# First tax year the taxpayer/spouse SSN requirement applies.
_FILER_SSN_REQUIRED_FROM = 2025


def ctc_parameters_for_year(tax_year: int, *, extrapolate: bool = False) -> CTCParameters:
    """Return CTC parameters for the requested tax year.

    With ``extrapolate=True``, years beyond the last published Rev. Proc. get
    CPI-extrapolated dollar amounts: the ACTC refundable cap (§24(d)(4)) and,
    since P.L. 119-21, the maximum credit (§24(i)), each rounded down to the
    nearest $100. The $200K/$400K phaseout thresholds are not indexed.

    Raises ``KeyError`` for unsupported years when not extrapolating.
    """
    ssn = tax_year >= _FILER_SSN_REQUIRED_FROM
    if tax_year in _CTC_REFUNDABLE_LIMIT_BY_YEAR:
        return CTCParameters(
            max_credit_per_child=_CTC_MAX_CREDIT_BY_YEAR[tax_year],
            refundable_limit_per_child=_CTC_REFUNDABLE_LIMIT_BY_YEAR[tax_year],
            require_filer_ssn=ssn,
        )
    if extrapolate and tax_year > max(_CTC_REFUNDABLE_LIMIT_BY_YEAR):
        from tax_modeler.credits.eitc import CREDIT_PARAM_CPI_GROWTH

        base_year = max(_CTC_REFUNDABLE_LIMIT_BY_YEAR)
        factor = (1.0 + CREDIT_PARAM_CPI_GROWTH) ** (tax_year - base_year)
        max_credit = int(_CTC_MAX_CREDIT_BY_YEAR[base_year] * factor // 100) * 100
        # The refundable portion cannot exceed the maximum credit.
        cap = min(
            int(_CTC_REFUNDABLE_LIMIT_BY_YEAR[base_year] * factor // 100) * 100,
            max_credit,
        )
        return CTCParameters(
            max_credit_per_child=max_credit,
            refundable_limit_per_child=cap,
            require_filer_ssn=ssn,
        )
    raise KeyError(
        f"CTC parameters not defined for tax year {tax_year}. "
        f"Supported years: {sorted(_CTC_REFUNDABLE_LIMIT_BY_YEAR)}"
        + ("" if extrapolate else " (pass extrapolate=True for later years)")
    )


def calculate_ctc(
    tax_unit: Dict,
    tax_year: int = 2023,
    *,
    extrapolate: bool = False,
) -> Dict[str, float]:
    """
    Calculate Child Tax Credit for a tax unit.

    Args:
        tax_unit: Dictionary containing tax unit information with keys:
            - filing_status: str ('single', 'married_filing_jointly', etc.)
            - income: float (Modified Adjusted Gross Income)
            - earned_income: float (falls back to ``income``)
            - dependents: List of dependent dictionaries
            - federal_tax_before_credits: optional float; computed from
              ``income`` and ``filing_status`` when absent
            - filer_has_ssn: optional bool (default True); from TY 2025 a
              unit without a filer SSN gets no CTC
        tax_year: Tax year for calculation (default 2023)
        extrapolate: CPI-extrapolate dollar parameters for years beyond the
            last published Rev. Proc. instead of raising KeyError.

    Returns:
        Dictionary with:
            - ctc_total: Total CTC allowed (nonrefundable + refundable)
            - ctc_nonrefundable: Portion that offsets federal income tax
            - ctc_refundable: Refundable portion (ACTC)
            - qualifying_children: Number of qualifying children
    """
    params = ctc_parameters_for_year(tax_year, extrapolate=extrapolate)

    # Initialize result
    result = {
        'ctc_total': 0.0,
        'ctc_nonrefundable': 0.0,
        'ctc_refundable': 0.0,
        'qualifying_children': 0
    }

    # Get qualifying children
    qualifying_children = _get_qualifying_children(tax_unit.get('dependents', []), params)
    n_children = len(qualifying_children)
    result['qualifying_children'] = n_children

    if n_children == 0:
        return result
    if params.require_filer_ssn and not _filer_has_ssn(tax_unit):
        return result

    # Calculate base credit amount
    base_credit = n_children * params.max_credit_per_child

    # Apply income phaseout
    filing_status = tax_unit.get('filing_status', 'single')
    income = tax_unit.get('income', 0)

    phased_out_credit = _apply_income_phaseout(
        base_credit, income, filing_status, params
    )

    # Nonrefundable part: limited to federal income tax before credits.
    tax_before_credits = tax_unit.get('federal_tax_before_credits')
    if tax_before_credits is None or pd.isna(tax_before_credits):
        from tax_modeler.liability.federal import federal_tax_before_credits

        tax_before_credits = float(federal_tax_before_credits(
            income or 0.0, filing_status, tax_year=tax_year, extrapolate=extrapolate,
        )[0])
    nonrefundable_portion = min(phased_out_credit, max(0.0, float(tax_before_credits)))

    # Refundable part (ACTC): the unused remainder, limited to 15% of earned
    # income above the threshold (once per return) and the per-child cap.
    earned_income = tax_unit.get('earned_income', tax_unit.get('income', 0))  # Fallback to total income if earned_income not available
    earned_income_limit = (
        max(0, earned_income - params.earned_income_threshold) * params.actc_earned_income_rate
    )
    refundable_portion = min(
        phased_out_credit - nonrefundable_portion,
        earned_income_limit,
        n_children * params.refundable_limit_per_child,
    )

    result.update({
        'ctc_total': nonrefundable_portion + refundable_portion,
        'ctc_nonrefundable': nonrefundable_portion,
        'ctc_refundable': refundable_portion
    })

    return result


def _filer_has_ssn(tax_unit: Dict) -> bool:
    """Taxpayer (or either spouse on a joint return) holds a work-eligible SSN.

    PUMS has no SSN/ITIN field, so this reads an imputed ``filer_has_ssn``
    flag and treats a missing value as True.
    """
    flag = tax_unit.get('filer_has_ssn', True)
    if flag is None or (isinstance(flag, float) and np.isnan(flag)):
        return True
    return bool(flag)


def _get_qualifying_children(dependents: List[Dict], params: CTCParameters) -> List[Dict]:
    """
    Identify qualifying children for CTC purposes.

    Args:
        dependents: List of dependent dictionaries
        params: CTC parameters

    Returns:
        List of qualifying children
    """
    qualifying = []

    for dependent in dependents:
        if _is_qualifying_child_ctc(dependent, params):
            qualifying.append(dependent)

    return qualifying


def _is_qualifying_child_ctc(dependent: Dict, params: CTCParameters) -> bool:
    """
    Check if a dependent qualifies for CTC.

    Args:
        dependent: Dependent information dictionary
        params: CTC parameters

    Returns:
        True if dependent qualifies for CTC
    """
    # Age test: Must be under 17 at end of tax year
    age = dependent.get('age', 0)
    if age >= params.qualifying_age_limit:
        return False

    # Relationship test: Must be a qualifying child relationship
    # For PUMS data, we'll use the relationship codes
    relationship = dependent.get('relationship', '')
    if not _is_qualifying_relationship(relationship):
        return False

    # SSN test: Must have valid SSN (simplified - assume all dependents have SSN)
    # In real implementation, would check SSN validity

    # Support test: Child must not provide more than half of own support
    # For PUMS data, we'll assume children under 17 don't provide own support

    # Citizenship test: Must be US citizen/national/resident alien
    # For PUMS data, we'll use citizenship status if available
    citizenship = dependent.get('citizenship', 'US')  # Default to US citizen
    if not _is_us_person(citizenship):
        return False

    return True


def _is_qualifying_relationship(relationship: str) -> bool:
    """
    Check if relationship qualifies for CTC.

    Args:
        relationship: PUMS relationship code or description

    Returns:
        True if relationship qualifies
    """
    # PUMS relationship codes that qualify:
    # 22 = Natural born child
    # 23 = Adopted child
    # 24 = Stepchild
    # 25 = Grandchild
    # 26 = Brother or sister
    # 27 = Father or mother
    # 28 = Grandparent
    # 29 = Parent-in-law
    # 30 = Son-in-law or daughter-in-law
    # 31 = Other relative
    # 32 = Roomer or boarder
    # 33 = Housemate or roommate
    # 34 = Unmarried partner
    # 35 = Foster child
    # 36 = Other nonrelative

    qualifying_codes = ['22', '23', '24', '25', '26', '35']  # Child, stepchild, grandchild, sibling, foster child

    # Handle both string and numeric relationship codes
    rel_str = str(relationship)
    return rel_str in qualifying_codes


def _is_us_person(citizenship: str) -> bool:
    """
    Check if person is US citizen, national, or resident alien.

    Args:
        citizenship: Citizenship status

    Returns:
        True if person qualifies as US person
    """
    # PUMS citizenship codes:
    # 1 = Born in the US
    # 2 = Born in Puerto Rico, Guam, the U.S. Virgin Islands, or the Northern Marianas
    # 3 = Born abroad of American parent(s)
    # 4 = U.S. citizen by naturalization
    # 5 = Not a citizen of the U.S.

    us_citizen_codes = ['1', '2', '3', '4', 'US']
    return str(citizenship) in us_citizen_codes


def _apply_income_phaseout(
    base_credit: float,
    income: float,
    filing_status: str,
    params: CTCParameters
) -> float:
    """
    Apply income-based phaseout to CTC.

    Args:
        base_credit: Base credit amount before phaseout
        income: Modified Adjusted Gross Income
        filing_status: Filing status
        params: CTC parameters

    Returns:
        Credit amount after phaseout
    """
    # Determine phaseout threshold based on filing status
    if filing_status in ['married_filing_jointly']:
        threshold = params.phaseout_threshold_joint
    else:
        threshold = params.phaseout_threshold_single

    # No phaseout if income is below threshold
    if income <= threshold:
        return base_credit

    # Calculate phaseout amount
    excess_income = income - threshold
    # Phaseout is $50 for every $1,000 (or fraction thereof) over threshold
    phaseout_amount = np.ceil(excess_income / 1000) * 50

    # Apply phaseout
    phased_out_credit = max(0, base_credit - phaseout_amount)

    return phased_out_credit


def with_federal_tax_before_credits(
    tax_units_df: pd.DataFrame,
    tax_year: int,
    *,
    extrapolate: bool = False,
) -> pd.DataFrame:
    """Add ``federal_tax_before_credits`` (vectorized) so per-row CTC calls
    skip recomputing it. Existing non-null values are kept."""
    from tax_modeler.liability.federal import federal_tax_before_credits

    if len(tax_units_df) == 0 or 'income' not in tax_units_df.columns:
        return tax_units_df
    out = tax_units_df.copy()
    status = (
        out['filing_status'].astype(str).to_numpy()
        if 'filing_status' in out.columns else np.full(len(out), 'single')
    )
    computed = federal_tax_before_credits(
        out['income'].fillna(0).astype(float).to_numpy(), status,
        tax_year=tax_year, extrapolate=extrapolate,
    )
    if 'federal_tax_before_credits' in out.columns:
        out['federal_tax_before_credits'] = out['federal_tax_before_credits'].fillna(
            pd.Series(computed, index=out.index)
        )
    else:
        out['federal_tax_before_credits'] = computed
    return out


def calculate_ctc_for_tax_units(
    tax_units_df: pd.DataFrame,
    tax_year: int = 2023,
) -> pd.DataFrame:
    """
    Calculate CTC for all tax units in a DataFrame.

    Args:
        tax_units_df: DataFrame containing tax unit information
        tax_year: Tax year for parameter selection (default 2023).

    Returns:
        DataFrame with CTC calculations added (prefixed ``ctc_``).
    """
    tax_units_df = with_federal_tax_before_credits(tax_units_df, tax_year=tax_year)
    results = []

    for _, tax_unit in tax_units_df.iterrows():
        tax_unit_dict = tax_unit.to_dict()
        ctc_result = calculate_ctc(tax_unit_dict, tax_year=tax_year)
        for key, value in ctc_result.items():
            tax_unit_dict[f'ctc_{key}'] = value
        results.append(tax_unit_dict)

    return pd.DataFrame(results)


def get_ctc_summary_stats(tax_units_df: pd.DataFrame) -> Dict[str, float]:
    """
    Calculate summary statistics for CTC across tax units.

    Args:
        tax_units_df: DataFrame with CTC calculations

    Returns:
        Dictionary with summary statistics
    """
    # Calculate CTC for all units if not already done
    if 'ctc_ctc_total' not in tax_units_df.columns:
        tax_units_df = calculate_ctc_for_tax_units(tax_units_df)

    stats = {
        'total_tax_units': len(tax_units_df),
        'units_with_ctc': len(tax_units_df[tax_units_df['ctc_ctc_total'] > 0]),
        'total_ctc_amount': tax_units_df['ctc_ctc_total'].sum(),
        'average_ctc_per_unit': tax_units_df['ctc_ctc_total'].mean(),
        'average_ctc_per_recipient': tax_units_df[tax_units_df['ctc_ctc_total'] > 0]['ctc_ctc_total'].mean(),
        'total_qualifying_children': tax_units_df['ctc_qualifying_children'].sum(),
        'total_refundable_ctc': tax_units_df['ctc_ctc_refundable'].sum(),
        'total_nonrefundable_ctc': tax_units_df['ctc_ctc_nonrefundable'].sum()
    }

    return stats
