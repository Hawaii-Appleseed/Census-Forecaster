"""
Hawaii State Tax Credits

Based on Hawaii Department of Taxation data and common state credits.
Major credits include:
- Food/Excise Tax Credit (refundable)
- Renewable Energy Technologies Credit
- Child and Dependent Care Credit
- Low-Income Household Renters Credit
"""

from __future__ import annotations

import math
import pandas as pd
import numpy as np
from typing import Dict, Optional


class HawaiiTaxCredits:
    """
    Calculate Hawaii state tax credits.
    
    Note: These are STATE credits, separate from federal credits.
    """
    
    def __init__(self, year: int = 2024):
        """Initialize with tax year"""
        self.year = year
    
    def food_excise_tax_credit(self, agi: float, filing_status: str,
                               num_dependents: int = 0) -> float:
        """
        Hawaii refundable food/excise tax credit (HRS §235-55.85).

        Statutory table for ``self.year`` (Act 163's for TY2023-2027, the
        prior table otherwise), via tax_modeler.credits.hi_food_excise.
        Exemptions: filer + spouse (joint) + dependents.
        """
        from tax_modeler.credits.hi_food_excise import food_excise_credit
        n_exemptions = 1 + num_dependents + (filing_status == 'married_filing_jointly')
        return food_excise_credit(agi, filing_status, n_exemptions, self.year)

    def renewable_energy_credit(self, agi: float, filing_status: str) -> float:
        """
        Renewable Energy Technologies Income Tax Credit.
        
        For solar, wind, and other renewable energy installations.
        This is highly variable based on actual installations.
        
        Estimate: ~2% of middle-to-high income filers claim this
        Average credit: ~$2,000
        """
        # Only middle-to-high income homeowners typically claim
        if agi < 50000 or agi > 300000:
            return 0
        
        # ~2% of eligible filers claim. Applied as an expected value (every
        # eligible filer gets 2% of the amount) rather than an unseeded
        # per-call coin flip, which made results vary run to run; aggregate
        # dollars are unchanged. Same smoothing as credits/hi_renters.py.
        claim_rate = 0.02
        if agi < 100000:
            return claim_rate * 1500
        elif agi < 200000:
            return claim_rate * 2500
        else:
            return claim_rate * 3500
    
    def child_dependent_care_credit(self, agi: float, filing_status: str,
                                    num_dependents: int = 0,
                                    credit_scenario: Optional[str] = None) -> float:
        """
        Hawaii Child and Dependent Care Tax Credit (HRS 235-55.6).

        Current law: expense caps $3k/$6k, 25% down to 20%, non-refundable,
                     phases out above $43k AGI.
        HB2306 HD1 enhanced: expense caps $10k/$20k, 50% down to 5%, refundable,
                     wider AGI schedule ($80k-$160k phase-down).
        SB3125 SD1 enhanced: expense caps $10k/$20k, formula-based (35% at $43k,
                     -1pp per $3k, floor 15%), refundable.
                     NOTE: Bill has blanks for starting % and AGI threshold.
                     Assumed: 35% starting at $43,000 AGI.

        Note: PUMS has no childcare expense data. We estimate expenses using
        Hawaii avg childcare cost (~$12k/yr/child). Most filers with qualifying
        dependents will hit the expense cap.
        """
        if num_dependents == 0:
            return 0

        if credit_scenario == 'hb2306_hd1':
            # HB2306 HD1 parameters (Section 235-55.6 as amended)
            expense_cap = 10_000 if num_dependents == 1 else 20_000

            # AGI-based percentage schedule
            if agi <= 80_000:
                pct = 0.50
            elif agi <= 90_000:
                pct = 0.45
            elif agi <= 100_000:
                pct = 0.40
            elif agi <= 110_000:
                pct = 0.35
            elif agi <= 120_000:
                pct = 0.30
            elif agi <= 130_000:
                pct = 0.25
            elif agi <= 140_000:
                pct = 0.20
            elif agi <= 150_000:
                pct = 0.15
            elif agi <= 160_000:
                pct = 0.10
            else:
                pct = 0.05

        elif credit_scenario == 'sb3125_sd1':
            # SB3125 SD1 §235-55.6: formula-based, refundable, $10k/$20k caps
            # Bill blanks assumed: 35% starting at $43,000 AGI, -1pp per $3k, floor 15%
            expense_cap = 10_000 if num_dependents == 1 else 20_000
            threshold = 43_000
            starting_pct = 0.35
            steps = math.ceil(max(0, agi - threshold) / 3_000)
            pct = max(0.15, starting_pct - 0.01 * steps)

        else:
            # Current law parameters
            expense_cap = 3_000 if num_dependents == 1 else 6_000

            # Current law percentage schedule (HRS 235-55.6 pre-amendment)
            if agi <= 25_000:
                pct = 0.25
            elif agi <= 43_000:
                # Linear decrease from 25% to 20% over $25k-$43k
                pct = 0.25 - 0.05 * (agi - 25_000) / 18_000
            else:
                pct = 0.20

            # Current law phases out entirely above $100k
            if agi > 100_000:
                return 0

        # Estimated qualifying expenses (Hawaii avg ~$12k/yr/child)
        estimated_expenses = min(12_000 * num_dependents, expense_cap)

        return estimated_expenses * pct
    
    def low_income_renters_credit(self, agi: float, filing_status: str) -> float:
        """
        Low-Income Household Renters Credit (refundable).
        
        For renters with low income.
        """
        # Income thresholds. (The separate-filer key was once misspelled
        # 'married_filing_separate', so separate filers fell to the 30K default.)
        max_income = {
            'single': 30000,
            'married_filing_jointly': 40000,
            'head_of_household': 35000,
            'married_filing_separately': 20000,
        }
        
        threshold = max_income.get(filing_status, 30000)
        
        if agi > threshold:
            return 0
        
        # Assume 30% of low-income filers are renters. Expected value, not an
        # unseeded coin flip (see renewable_energy_credit); matches the
        # deterministic take-up smoothing in credits/hi_renters.py.
        renter_share = 0.30
        if agi < 15000:
            return renter_share * 150
        elif agi < 25000:
            return renter_share * 100
        else:
            return renter_share * 50
    
    def calculate_total_credits(self, agi: float, filing_status: str,
                               num_dependents: int = 0,
                               tax_before_credits: float = 0,
                               credit_scenario: Optional[str] = None) -> Dict[str, float]:
        """
        Calculate all Hawaii state tax credits.

        Args:
            agi: Adjusted Gross Income
            filing_status: Filing status
            num_dependents: Number of dependents
            tax_before_credits: Tax liability before credits
            credit_scenario: If 'hb2306_hd1', use enhanced CDCC (refundable)

        Returns:
            Dictionary with individual credits and totals
        """
        enhanced_cdcc = credit_scenario in ('hb2306_hd1', 'sb3125_sd1')

        credits = {
            'food_excise': self.food_excise_tax_credit(agi, filing_status, num_dependents),
            'renewable_energy': self.renewable_energy_credit(agi, filing_status),
            'child_care': self.child_dependent_care_credit(agi, filing_status, num_dependents,
                                                           credit_scenario=credit_scenario),
            'renters': self.low_income_renters_credit(agi, filing_status),
        }

        # Separate refundable and non-refundable
        # Under HB2306 HD1 and SB3125 SD1, CDCC becomes refundable
        if enhanced_cdcc:
            refundable_credits = credits['food_excise'] + credits['renters'] + credits['child_care']
            nonrefundable_credits = credits['renewable_energy']
        else:
            refundable_credits = credits['food_excise'] + credits['renters']
            nonrefundable_credits = credits['renewable_energy'] + credits['child_care']

        # Non-refundable credits limited to tax liability
        nonrefundable_credits = min(nonrefundable_credits, tax_before_credits)

        credits['total_refundable'] = refundable_credits
        credits['total_nonrefundable'] = nonrefundable_credits
        credits['total'] = refundable_credits + nonrefundable_credits

        return credits

    def total_credits_arrays(self, agi: np.ndarray, filing_status: np.ndarray,
                             num_dependents: np.ndarray, tax_before_credits: np.ndarray,
                             credit_scenario: Optional[str] = None,
                             ) -> tuple[np.ndarray, np.ndarray]:
        """:meth:`calculate_total_credits` for arrays of units: ``(total, child_care)``.

        The same arithmetic in the same order, so each unit's floats equal the
        scalar method's. Python's ``max(a, b)``/``min(a, b)`` keep ``a`` unless
        ``b`` is strictly greater/smaller, which differs from ``np.maximum``/
        ``np.minimum`` on signed zeros and NaN; ``_pymax``/``_pymin`` keep that.
        Callers must pass finite ``agi`` and ``tax_before_credits`` (the scalar
        method covers the rest; see ``TaxCalculator.unit_liabilities``).
        """
        from tax_modeler.credits.hi_food_excise import (
            _JOINT_TABLE_STATUSES,
            _per_exemption,
            hawaii_food_excise_parameters,
        )

        def _pymax(a, b):
            return np.where(b > a, b, a)

        def _pymin(a, b):
            return np.where(b < a, b, a)

        agi = np.asarray(agi, dtype=float)
        status = np.asarray(filing_status, dtype=object)
        deps = np.asarray(num_dependents, dtype=np.int64)
        tax = np.asarray(tax_before_credits, dtype=float)
        joint = status == 'married_filing_jointly'

        # Food/excise (hi_food_excise.food_excise_credit)
        p = hawaii_food_excise_parameters(self.year)
        n_ex = 1 + deps + joint.astype(np.int64)
        clipped = _pymax(agi, 0.0)
        per_ex = np.zeros(len(agi), dtype=float)
        joint_table = np.isin(status, list(_JOINT_TABLE_STATUSES))
        for table, m in ((p.joint, joint_table), (p.single, ~joint_table)):
            if m.any():
                per_ex[m] = _per_exemption(clipped[m], table, p.income_threshold_factor)
        food = per_ex * _pymax(n_ex, 0) * p.amount_pct

        # Renewable energy
        renewable = np.where((agi < 50000) | (agi > 300000), 0.0,
                             np.where(agi < 100000, 0.02 * 1500,
                                      np.where(agi < 200000, 0.02 * 2500, 0.02 * 3500)))

        # Child and dependent care
        if credit_scenario == 'hb2306_hd1':
            cap = np.where(deps == 1, 10_000, 20_000)
            pct = np.select(
                [agi <= 80_000, agi <= 90_000, agi <= 100_000, agi <= 110_000, agi <= 120_000,
                 agi <= 130_000, agi <= 140_000, agi <= 150_000, agi <= 160_000],
                [0.50, 0.45, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.10], 0.05)
            phased_out = np.zeros(len(agi), dtype=bool)
        elif credit_scenario == 'sb3125_sd1':
            cap = np.where(deps == 1, 10_000, 20_000)
            steps = np.ceil(_pymax(0, agi - 43_000) / 3_000)
            pct = _pymax(0.15, 0.35 - 0.01 * steps)
            phased_out = np.zeros(len(agi), dtype=bool)
        else:
            cap = np.where(deps == 1, 3_000, 6_000)
            pct = np.where(agi <= 25_000, 0.25,
                           np.where(agi <= 43_000, 0.25 - 0.05 * (agi - 25_000) / 18_000, 0.20))
            phased_out = agi > 100_000
        expenses = _pymin(12_000 * deps, cap)
        child_care = np.where((deps == 0) | phased_out, 0.0, expenses * pct)

        # Low-income renters
        threshold = np.full(len(agi), 30000.0)
        for fs, t in (('single', 30000), ('married_filing_jointly', 40000),
                      ('head_of_household', 35000), ('married_filing_separately', 20000)):
            threshold[status == fs] = t
        renters = np.where(agi > threshold, 0.0,
                           np.where(agi < 15000, 0.30 * 150,
                                    np.where(agi < 25000, 0.30 * 100, 0.30 * 50)))

        if credit_scenario in ('hb2306_hd1', 'sb3125_sd1'):
            refundable = food + renters + child_care
            nonrefundable = renewable
        else:
            refundable = food + renters
            nonrefundable = renewable + child_care
        nonrefundable = _pymin(nonrefundable, tax)
        return refundable + nonrefundable, child_care


def calculate_hawaii_credits(agi: float, filing_status: str,
                             num_dependents: int = 0,
                             tax_before_credits: float = 0,
                             year: int = 2024,
                             credit_scenario: Optional[str] = None) -> Dict[str, float]:
    """
    Convenience function to calculate Hawaii state tax credits.

    Args:
        agi: Adjusted Gross Income
        filing_status: Filing status
        num_dependents: Number of dependents
        tax_before_credits: Tax liability before credits
        year: Tax year
        credit_scenario: If 'hb2306_hd1', use enhanced CDCC

    Returns:
        Dictionary with credit amounts
    """
    calculator = HawaiiTaxCredits(year)
    return calculator.calculate_total_credits(agi, filing_status, num_dependents,
                                              tax_before_credits, credit_scenario)


def apply_credits_to_dataframe(df: pd.DataFrame,
                               agi_col: str = 'agi',
                               filing_status_col: str = 'filing_status',
                               dependents_col: str = 'num_dependents',
                               tax_col: str = 'tax_liability',
                               credit_scenario: Optional[str] = None) -> pd.DataFrame:
    """
    Apply Hawaii tax credits to a DataFrame of tax units.

    Args:
        df: DataFrame with tax units
        agi_col: Name of AGI column
        filing_status_col: Name of filing status column
        dependents_col: Name of dependents column
        tax_col: Name of tax liability column
        credit_scenario: If 'hb2306_hd1', use enhanced CDCC

    Returns:
        DataFrame with added credit columns
    """
    result_df = df.copy()
    calculator = HawaiiTaxCredits()

    credits_list = []
    for _, row in df.iterrows():
        agi = row[agi_col]
        filing_status = row[filing_status_col]
        num_dependents = row.get(dependents_col, 0)
        tax_before_credits = row[tax_col]

        credits = calculator.calculate_total_credits(
            agi, filing_status, num_dependents, tax_before_credits, credit_scenario
        )
        credits_list.append(credits)
    
    # Add credit columns
    credits_df = pd.DataFrame(credits_list)
    for col in credits_df.columns:
        result_df[f'hi_credit_{col}'] = credits_df[col]
    
    # Calculate net tax after credits
    result_df['hi_tax_after_credits'] = (
        result_df[tax_col] - 
        result_df['hi_credit_total_nonrefundable'] - 
        result_df['hi_credit_total_refundable']
    )
    
    return result_df


if __name__ == '__main__':
    # Example usage
    calculator = HawaiiTaxCredits(2024)
    
    # Test cases
    test_cases = [
        (20000, 'single', 0, 500, "Low-income single, no dependents"),
        (35000, 'head_of_household', 2, 1500, "HoH with 2 kids"),
        (75000, 'married_filing_jointly', 1, 3000, "Joint filers, 1 child"),
        (150000, 'married_filing_jointly', 0, 8000, "High-income joint, no kids"),
    ]
    
    print("Hawaii State Tax Credits Examples:")
    print("="*80)
    
    for agi, status, deps, tax, desc in test_cases:
        credits = calculator.calculate_total_credits(agi, status, deps, tax)
        
        print(f"\n{desc}")
        print(f"  AGI: ${agi:,} | Status: {status} | Dependents: {deps} | Tax: ${tax:,}")
        print(f"  Food/Excise:      ${credits['food_excise']:>8,.2f}")
        print(f"  Renewable Energy: ${credits['renewable_energy']:>8,.2f}")
        print(f"  Child Care:       ${credits['child_care']:>8,.2f}")
        print(f"  Renters:          ${credits['renters']:>8,.2f}")
        print(f"  Total Credits:    ${credits['total']:>8,.2f}")
        print(f"  Net Tax:          ${max(0, tax - credits['total']):>8,.2f}")
