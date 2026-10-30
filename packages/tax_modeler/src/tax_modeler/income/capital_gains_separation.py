"""
Capital Gains Income Separation Module

Separates capital gains income from regular income based on DOTAX SOI 2022 Table 21.
This module applies capital gains percentages by AGI bracket to separate total income
into regular income and capital gains components.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# Load capital gains percentages from DOTAX SOI Table 21
_CAP_GAINS_DATA_PATH = Path(__file__).parent.parent.parent.parent / 'data' / 'processed' / 'capital_gains_percentages.csv'

def load_capital_gains_percentages():
    """
    Load capital gains percentages by AGI bracket from DOTAX SOI 2022 Table 21.
    
    Returns:
        DataFrame with columns: agi_min, agi_max, cap_gains_pct
    """
    if not _CAP_GAINS_DATA_PATH.exists():
        raise FileNotFoundError(
            f"Capital gains data not found at {_CAP_GAINS_DATA_PATH}. "
            "Please ensure the file exists."
        )
    
    df = pd.read_csv(_CAP_GAINS_DATA_PATH)
    logger.info(f"Loaded capital gains percentages for {len(df)} AGI brackets")
    return df


def get_capital_gains_percentage(agi, cap_gains_df=None):
    """
    Get the capital gains percentage for a given AGI amount.
    
    Args:
        agi: Adjusted Gross Income amount
        cap_gains_df: DataFrame with capital gains percentages (loaded if None)
        
    Returns:
        float: Percentage of income that is capital gains (0.0 to 1.0)
    """
    if cap_gains_df is None:
        cap_gains_df = load_capital_gains_percentages()
    
    # Handle negative or zero AGI
    if agi <= 0:
        return 0.0
    
    # Find the matching bracket
    mask = (cap_gains_df['agi_min'] <= agi) & (agi < cap_gains_df['agi_max'])
    matches = cap_gains_df[mask]
    
    if len(matches) == 0:
        # If AGI exceeds highest bracket, use highest bracket percentage
        return cap_gains_df['cap_gains_pct'].iloc[-1]
    
    return matches['cap_gains_pct'].iloc[0]


def apply_capital_gains_separation(
    tax_units,
    agi_col='agi',
    income_col='income',
    output_regular_col='regular_income',
    output_cap_gains_col='capital_gains_income',
    inplace=False
):
    """
    Separate income into regular income and capital gains components.
    
    This applies the DOTAX SOI 2022 Table 21 capital gains percentages by AGI bracket
    to split total income into:
    - Regular income (wages, business income, etc.)
    - Capital gains income
    
    Args:
        tax_units: DataFrame with tax unit data
        agi_col: Column name containing AGI (default: 'agi')
        income_col: Column name containing total income (default: 'income')
        output_regular_col: Output column name for regular income (default: 'regular_income')
        output_cap_gains_col: Output column name for capital gains (default: 'capital_gains_income')
        inplace: If True, modify DataFrame in place (default: False)
        
    Returns:
        DataFrame with added columns for regular_income and capital_gains_income
        
    Example:
        >>> tax_units = apply_capital_gains_separation(tax_units)
        >>> print(tax_units[['agi', 'income', 'regular_income', 'capital_gains_income']].head())
    """
    if not inplace:
        tax_units = tax_units.copy()
    
    # Ensure AGI column exists
    if agi_col not in tax_units.columns:
        if 'total_income' in tax_units.columns:
            logger.info(f"'{agi_col}' column not found, estimating from 'total_income'")
            tax_units[agi_col] = tax_units['total_income'] * 0.99
        elif income_col in tax_units.columns:
            logger.info(f"'{agi_col}' column not found, estimating from '{income_col}'")
            tax_units[agi_col] = tax_units[income_col] * 0.99
        else:
            raise ValueError(f"Column '{agi_col}' not found and cannot be estimated")
    
    # Ensure income column exists
    if income_col not in tax_units.columns:
        if 'total_income' in tax_units.columns:
            income_col = 'total_income'
        else:
            raise ValueError(f"Column '{income_col}' not found")
    
    # Load capital gains percentages
    cap_gains_df = load_capital_gains_percentages()
    
    logger.info(f"Applying capital gains separation to {len(tax_units):,} tax units...")
    logger.info(f"  AGI column: {agi_col}")
    logger.info(f"  Income column: {income_col}")
    
    # Apply capital gains percentage to each tax unit
    tax_units['_cap_gains_pct'] = tax_units[agi_col].apply(
        lambda agi: get_capital_gains_percentage(agi, cap_gains_df)
    )
    
    # Calculate capital gains and regular income
    tax_units[output_cap_gains_col] = tax_units[income_col] * tax_units['_cap_gains_pct']
    tax_units[output_regular_col] = tax_units[income_col] - tax_units[output_cap_gains_col]
    
    # Clean up temporary column
    tax_units.drop('_cap_gains_pct', axis=1, inplace=True)
    
    # Summary statistics
    total_income = (tax_units[income_col] * tax_units.get('weight', 1)).sum()
    total_cap_gains = (tax_units[output_cap_gains_col] * tax_units.get('weight', 1)).sum()
    total_regular = (tax_units[output_regular_col] * tax_units.get('weight', 1)).sum()
    
    cap_gains_pct = (total_cap_gains / total_income * 100) if total_income > 0 else 0
    
    logger.info("✅ Capital gains separation complete:")
    logger.info(f"   Total income:        ${total_income/1e9:.2f}B")
    logger.info(f"   Regular income:      ${total_regular/1e9:.2f}B ({100-cap_gains_pct:.1f}%)")
    logger.info(f"   Capital gains:       ${total_cap_gains/1e9:.2f}B ({cap_gains_pct:.1f}%)")
    logger.info("   Expected cap gains:  7.4% (DOTAX SOI 2022)")
    
    return tax_units


def get_capital_gains_summary(tax_units, agi_col='agi', weight_col='weight'):
    """
    Generate a summary of capital gains distribution by AGI bracket.
    
    Args:
        tax_units: DataFrame with tax units (must have capital gains columns)
        agi_col: Column name for AGI
        weight_col: Column name for weights
        
    Returns:
        DataFrame with summary by AGI bracket
    """
    if 'capital_gains_income' not in tax_units.columns:
        raise ValueError("Tax units must have 'capital_gains_income' column. Run apply_capital_gains_separation() first.")
    
    # Assign AGI brackets
    def assign_bracket(agi):
        if agi < 10000: return '$0-10k'
        elif agi < 20000: return '$10-20k'
        elif agi < 30000: return '$20-30k'
        elif agi < 40000: return '$30-40k'
        elif agi < 50000: return '$40-50k'
        elif agi < 75000: return '$50-75k'
        elif agi < 100000: return '$75-100k'
        elif agi < 150000: return '$100-150k'
        elif agi < 200000: return '$150-200k'
        elif agi < 300000: return '$200-300k'
        elif agi < 400000: return '$300-400k'
        else: return '$400k+'
    
    tax_units['_bracket'] = tax_units[agi_col].apply(assign_bracket)
    
    # Calculate weighted sums by bracket
    summary = tax_units.groupby('_bracket').apply(
        lambda g: pd.Series({
            'returns': g[weight_col].sum(),
            'total_income': (g['income'] * g[weight_col]).sum(),
            'regular_income': (g['regular_income'] * g[weight_col]).sum(),
            'capital_gains': (g['capital_gains_income'] * g[weight_col]).sum()
        })
    )
    
    summary['cap_gains_pct'] = (summary['capital_gains'] / summary['total_income'] * 100).round(2)
    
    # Sort by income bracket
    bracket_order = ['$0-10k', '$10-20k', '$20-30k', '$30-40k', '$40-50k', '$50-75k',
                     '$75-100k', '$100-150k', '$150-200k', '$200-300k', '$300-400k', '$400k+']
    summary = summary.reindex([b for b in bracket_order if b in summary.index])
    
    # Clean up
    tax_units.drop('_bracket', axis=1, inplace=True)
    
    return summary


if __name__ == '__main__':
    # Test the module
    logging.basicConfig(level=logging.INFO, format='%(levelname)s - %(message)s')
    
    # Create synthetic test data
    np.random.seed(42)
    test_data = pd.DataFrame({
        'filer_id': range(100),
        'agi': np.random.lognormal(10.5, 1.2, 100),
        'income': np.random.lognormal(10.5, 1.2, 100),
        'weight': np.random.randint(1, 100, 100)
    })
    
    print("Testing capital gains separation...")
    print(f"\nTest data: {len(test_data)} tax units")
    print(f"AGI range: ${test_data['agi'].min():,.0f} - ${test_data['agi'].max():,.0f}")
    
    # Apply separation
    result = apply_capital_gains_separation(test_data)
    
    # Show results
    print("\nSample results:")
    print(result[['agi', 'income', 'regular_income', 'capital_gains_income']].head(10))
    
    # Show summary
    print("\nSummary by bracket:")
    summary = get_capital_gains_summary(result)
    print(summary)
