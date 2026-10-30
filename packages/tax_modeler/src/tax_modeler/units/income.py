"""
Income calculation utilities.

This module provides functions for calculating different types of income
for tax purposes, including adjustments for inflation and wage growth
to project to 2026.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from tax_modeler.config.income_growth import apply_income_growth


def extract_person_income_components(person: pd.Series) -> dict[str, float]:
    """
    Extract individual income components adjusted by ADJINC, without growth projection.

    Returns dict with ADJINC-adjusted values for each PUMS income source,
    plus the person's age (AGEP).
    """
    adjinc_raw = float(person.get('ADJINC', 1_000_000) or 1_000_000)
    adjinc = adjinc_raw / 1_000_000

    ssp_gross = float(person.get('SSP', 0) or 0) * adjinc
    return {
        'wagp':     float(person.get('WAGP', 0) or 0) * adjinc,
        'semp':     float(person.get('SEMP', 0) or 0) * adjinc,
        'intp':     float(person.get('INTP', 0) or 0) * adjinc,
        'div':      float(person.get('DIV', 0) or 0) * adjinc,
        'retp':     float(person.get('RETP', 0) or 0) * adjinc,
        'ssp':      ssp_gross * 0.85,   # 85% taxable — used for AGI/tax calc
        'ssp_full': ssp_gross,           # 100% — used for total-cash-income (TCI)
        'oip':      float(person.get('OIP', 0) or 0) * adjinc,
        'ssip':     float(person.get('SSIP', 0) or 0) * adjinc,  # SSI benefits
        'pap':      float(person.get('PAP', 0) or 0) * adjinc,   # Public assistance
        'agep':     int(person.get('AGEP', 0) or 0),
    }

def calculate_person_income(
    person: pd.Series, 
    apply_2026_growth: bool = True,
    is_resident: bool = True
) -> float:
    """
    Calculate total income for a single person.
    
    Args:
        person: Series containing person data
        apply_2026_growth: If True, apply income growth to project to 2026
        is_resident: True for Hawaii residents (2023→2026), False for nonresidents (2022→2026)
        
    Returns:
        float: Total income (optionally projected to 2026)
        
    Note:
        This function uses individual income components (WAGP, SEMP, etc.)
        rather than PINCP to avoid potential double-counting issues.
        
        PUMS 2023 data is already adjusted to 2023 dollars via ADJINC.
        If apply_2026_growth=True, we further adjust to 2026 using:
        - Residents: 5.6% real growth (2023→2026)
        - Nonresidents: 5.3% real growth (2022→2026)
    """
    # Initialize total income
    total_income = 0.0
    
    # Add up all income components
    total_income += float(person.get('WAGP', 0) or 0)  # Wage/salary income
    total_income += float(person.get('SEMP', 0) or 0)  # Self-employment income
    total_income += float(person.get('INTP', 0) or 0)  # Interest income
    total_income += float(person.get('DIV', 0) or 0)   # Dividend income
    
    # Retirement income
    total_income += float(person.get('RETP', 0) or 0)
    
    # Social Security benefits (only taxable portion)
    total_income += float(person.get('SSP', 0) or 0) * 0.85  # 85% is taxable
    
    # Other income
    total_income += float(person.get('OIP', 0) or 0)
    
    # Apply ADJINC (adjustment factor for income)
    # CRITICAL: ADJINC in PUMS is stored as integer (e.g., 1184371 = 1.184371)
    # Must divide by 1,000,000 to get the actual adjustment factor
    # This adjusts income to the PUMS survey year (2023 for 2023 PUMS)
    adjinc_raw = float(person.get('ADJINC', 1000000) or 1000000)
    adjinc = adjinc_raw / 1000000.0  # Convert from integer to decimal factor
    total_income *= adjinc
    
    # Apply 2026 growth projection if requested
    if apply_2026_growth:
        total_income = apply_income_growth(total_income, is_resident)
    
    return total_income

def calculate_tax_unit_income(
    tax_unit: pd.DataFrame,
    apply_2026_growth: bool = True,
    is_resident: bool = True
) -> float:
    """
    Calculate total income for a tax filing unit.
    
    Args:
        tax_unit: DataFrame containing persons in a tax filing unit
        apply_2026_growth: If True, apply income growth to project to 2026
        is_resident: True for Hawaii residents, False for nonresidents
        
    Returns:
        float: Total tax unit income (optionally projected to 2026)
    """
    if tax_unit.empty:
        return 0.0
        
    # Calculate income for each person and sum
    return tax_unit.apply(
        lambda person: calculate_person_income(person, apply_2026_growth, is_resident),
        axis=1
    ).sum()


def calculate_persons_income(
    persons: list[pd.Series | dict],
    apply_2026_growth: bool = True,
    is_resident: bool = True,
) -> float:
    """``calculate_tax_unit_income(pd.DataFrame(persons), ...)`` without
    building the frame: the same per-person incomes, summed the way
    ``Series.sum`` sums them (NaN counted as 0, numpy's addition order).
    The tax-unit constructor calls this once or twice per household, where
    building a one- or two-row frame cost more than the income itself."""
    if not persons:
        return 0.0
    incomes = [calculate_person_income(p, apply_2026_growth, is_resident) for p in persons]
    return np.add.reduce(np.array([0.0 if x != x else x for x in incomes], dtype=float))
