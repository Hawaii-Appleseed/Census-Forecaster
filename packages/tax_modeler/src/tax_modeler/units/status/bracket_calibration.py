"""
Income Bracket-Level SOI Calibration

This module implements sophisticated calibration that matches BOTH:
1. Filing status totals (single, MFJ, HoH, MFS)
2. Income bracket distributions within each filing status

This ensures accurate representation across the full income distribution.
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


# SOI Benchmark Data from Table 13A, 13B, 13C (Hawaii DOTAX 2022)
SOI_BENCHMARKS = {
    'single_and_mfs': {
        'total': 351205,
        'brackets': [
            (0, 2400, 82072),
            (2400, 4800, 13773),
            (4800, 9600, 24317),
            (9600, 14400, 20638),
            (14400, 19200, 18647),
            (19200, 24000, 18863),
            (24000, 36000, 47514),
            (36000, 48000, 40454),
            (48000, 150000, 76914),
            (150000, 175000, 2060),
            (175000, 200000, 1220),
            (200000, float('inf'), 4733),
        ]
    },
    'married_filing_jointly': {
        'total': 216358,
        'brackets': [
            (0, 4800, 40236),
            (4800, 9600, 5632),
            (9600, 19200, 10798),
            (19200, 28800, 11317),
            (28800, 38400, 11443),
            (38400, 48000, 10914),
            (48000, 72000, 27330),
            (72000, 96000, 26226),
            (96000, 300000, 62358),
            (300000, 350000, 2401),
            (350000, 400000, 1588),
            (400000, float('inf'), 6115),
        ]
    },
    'head_of_household': {
        'total': 67393,
        'brackets': [
            (0, 3600, 6671),
            (3600, 7200, 2619),
            (7200, 14400, 5543),
            (14400, 21600, 5904),
            (21600, 28800, 7376),
            (28800, 36000, 7732),
            (36000, 54000, 13878),
            (54000, 72000, 7835),
            (72000, 225000, 9117),
            (225000, 262500, 176),
            (262500, 300000, 118),
            (300000, float('inf'), 424),
        ]
    }
}

# Standard deductions for 2022 tax year
STANDARD_DEDUCTIONS = {
    'single': 12950,
    'married_filing_jointly': 25900,
    'married_filing_separately': 12950,
    'head_of_household': 19400
}


def calculate_taxable_income(tax_units: pd.DataFrame) -> pd.DataFrame:
    """
    Calculate taxable income for each tax unit.
    
    Args:
        tax_units: DataFrame with 'income' and 'filing_status' columns
        
    Returns:
        DataFrame with added 'taxable_income' column
    """
    df = tax_units.copy()
    
    # Map standard deduction by filing status
    df['standard_deduction'] = df['filing_status'].map(STANDARD_DEDUCTIONS)
    
    # Calculate taxable income (AGI - standard deduction, floored at 0)
    df['taxable_income'] = (df['income'] - df['standard_deduction']).clip(lower=0)
    
    return df


def assign_income_bracket(taxable_income: float, brackets: list[tuple[float, float, int]]) -> int:
    """
    Assign income bracket index for a given taxable income.
    
    Args:
        taxable_income: Taxable income amount
        brackets: List of (min, max, soi_count) tuples
        
    Returns:
        Bracket index (0-based)
    """
    for i, (min_inc, max_inc, _) in enumerate(brackets):
        if max_inc == float('inf'):
            if taxable_income >= min_inc:
                return i
        else:
            if min_inc <= taxable_income < max_inc:
                return i
    
    # Default to first bracket if no match
    return 0


def calculate_bracket_factors(tax_units: pd.DataFrame, 
                              filing_status: str,
                              weight_col: str = 'weight',
                              target_split: dict[str, float] | None = None) -> dict[int, float]:
    """
    Calculate calibration factors for each income bracket within a filing status.
    
    Args:
        tax_units: DataFrame with tax units
        filing_status: Filing status to calibrate
        weight_col: Column name for weights
        target_split: For single/MFS, the target split ratio (e.g., {'single': 0.95, 'mfs': 0.05})
        
    Returns:
        Dictionary mapping bracket_index -> calibration_factor
    """
    # Get SOI benchmark for this filing status
    if filing_status in ['single', 'married_filing_separately']:
        benchmark = SOI_BENCHMARKS['single_and_mfs']
        
        # For single/MFS, we need to split the benchmark based on current proportions
        # or provided target split
        if target_split is None:
            # Use current proportions to split the benchmark
            single_count = tax_units[tax_units['filing_status'] == 'single'][weight_col].sum()
            mfs_count = tax_units[tax_units['filing_status'] == 'married_filing_separately'][weight_col].sum()
            total = single_count + mfs_count
            
            if total > 0:
                if filing_status == 'single':
                    split_ratio = single_count / total
                else:
                    split_ratio = mfs_count / total
            else:
                split_ratio = 0.95 if filing_status == 'single' else 0.05  # Default split
        else:
            split_ratio = target_split.get(filing_status, 0.5)
        
        # Apply split ratio to SOI benchmarks
        brackets = [(min_inc, max_inc, int(soi_count * split_ratio)) 
                   for min_inc, max_inc, soi_count in benchmark['brackets']]
    elif filing_status == 'married_filing_jointly':
        benchmark = SOI_BENCHMARKS['married_filing_jointly']
        brackets = benchmark['brackets']
    elif filing_status == 'head_of_household':
        benchmark = SOI_BENCHMARKS['head_of_household']
        brackets = benchmark['brackets']
    else:
        logger.warning(f"Unknown filing status: {filing_status}")
        return {}
    
    # Filter to this filing status
    df = tax_units[tax_units['filing_status'] == filing_status].copy()
    
    if df.empty:
        return {}
    
    # Assign brackets
    df['bracket'] = df['taxable_income'].apply(
        lambda x: assign_income_bracket(x, benchmark['brackets'])
    )
    
    # Calculate current weighted counts by bracket
    current_counts = df.groupby('bracket')[weight_col].sum()
    
    # Calculate calibration factors
    factors = {}
    for i, (min_inc, max_inc, soi_count) in enumerate(brackets):
        current = current_counts.get(i, 0)
        
        if current > 0 and soi_count > 0:
            factor = soi_count / current
        else:
            factor = 1.0  # No adjustment if no current units or no target
        
        factors[i] = factor
        
        # Log significant adjustments
        if abs(factor - 1.0) > 0.2:  # More than 20% adjustment
            logger.debug(f"  {filing_status} bracket {i} ({min_inc}-{max_inc}): "
                        f"factor={factor:.3f} (current={current:.0f}, target={soi_count})")
    
    return factors


def apply_bracket_calibration(tax_units: pd.DataFrame,
                              weight_col: str = 'weight',
                              method: str = 'factor') -> pd.DataFrame:
    """
    Apply income bracket-level calibration to match SOI benchmarks.
    
    This function calibrates weights at the (filing_status, income_bracket) level
    to ensure both filing status totals AND income distributions match SOI.
    
    Args:
        tax_units: DataFrame of tax units
        weight_col: Column name for weights
        method: Calibration method:
                - 'factor': Multiply weights by calibration factors (recommended)
                - 'resample': Add/remove tax units to match targets (more disruptive)
        
    Returns:
        DataFrame with calibrated weights in 'weight_calibrated' column
    """
    logger.info("="*80)
    logger.info("BRACKET-LEVEL SOI CALIBRATION")
    logger.info("="*80)
    
    # Calculate taxable income
    df = calculate_taxable_income(tax_units)
    
    # Store original weights
    if 'weight_original' not in df.columns:
        df['weight_original'] = df[weight_col].copy()
    
    # Initialize calibrated weight
    df['weight_calibrated'] = df[weight_col].copy()
    df['calibration_factor'] = 1.0
    df['bracket'] = -1
    
    # Process each filing status
    filing_statuses = ['single', 'married_filing_separately', 
                      'married_filing_jointly', 'head_of_household']
    
    logger.info(f"\nMethod: {method}")
    logger.info(f"Processing {len(df):,} tax units with total weight {df[weight_col].sum():,.0f}")
    
    for status in filing_statuses:
        status_df = df[df['filing_status'] == status]
        
        if status_df.empty:
            continue
        
        logger.info(f"\n--- {status.upper().replace('_', ' ')} ---")
        logger.info(f"Units: {len(status_df):,} (weight: {status_df[weight_col].sum():,.0f})")
        
        # Calculate calibration factors for this filing status
        factors = calculate_bracket_factors(df, status, weight_col)
        
        if not factors:
            continue
        
        # Get brackets for this filing status
        if status in ['single', 'married_filing_separately']:
            brackets = SOI_BENCHMARKS['single_and_mfs']['brackets']
        elif status == 'married_filing_jointly':
            brackets = SOI_BENCHMARKS['married_filing_jointly']['brackets']
        elif status == 'head_of_household':
            brackets = SOI_BENCHMARKS['head_of_household']['brackets']
        else:
            continue
        
        # Assign brackets and apply factors
        for idx in status_df.index:
            taxable_inc = df.loc[idx, 'taxable_income']
            bracket_idx = assign_income_bracket(taxable_inc, brackets)
            
            df.loc[idx, 'bracket'] = bracket_idx
            
            if method == 'factor':
                # Apply calibration factor to weight
                factor = factors.get(bracket_idx, 1.0)
                df.loc[idx, 'calibration_factor'] = factor
                df.loc[idx, 'weight_calibrated'] = df.loc[idx, weight_col] * factor
        
        # Report results for this status
        status_after = df[df['filing_status'] == status]
        logger.info(f"Calibrated weight: {status_after['weight_calibrated'].sum():,.0f}")
        
        # Show bracket-level changes
        logger.info("\nBracket-level calibration factors:")
        for i, (min_inc, max_inc, soi_count) in enumerate(brackets):
            bracket_data = status_after[status_after['bracket'] == i]
            if len(bracket_data) > 0:
                avg_factor = bracket_data['calibration_factor'].mean()
                before = bracket_data[weight_col].sum()
                after = bracket_data['weight_calibrated'].sum()
                
                if max_inc == float('inf'):
                    label = f"${min_inc:,}+"
                else:
                    label = f"${min_inc:,}-${max_inc:,}"
                
                logger.info(f"  {label:20s}: {before:>10,.0f} -> {after:>10,.0f} "
                           f"(target: {soi_count:>8,}, factor: {avg_factor:.3f})")
    
    # Final statistics
    logger.info("\n" + "="*80)
    logger.info("CALIBRATION SUMMARY")
    logger.info("="*80)
    
    total_before = df[weight_col].sum()
    total_after = df['weight_calibrated'].sum()
    
    logger.info(f"\nTotal weight: {total_before:,.0f} -> {total_after:,.0f}")
    logger.info(f"Change: {total_after - total_before:+,.0f} ({(total_after - total_before)/total_before*100:+.1f}%)")
    
    logger.info("\nFiling Status Distribution:")
    logger.info(f"{'Status':<30} {'Before':>12} {'After':>12} {'Target':>12} {'Gap':>10}")
    logger.info("-"*80)
    
    for status in filing_statuses:
        before = df[df['filing_status'] == status][weight_col].sum()
        after = df[df['filing_status'] == status]['weight_calibrated'].sum()
        
        # Get target from SOI
        if status in ['single', 'married_filing_separately']:
            # For single/MFS, we need to estimate the split
            # Use current ratio as proxy
            single_count = df[df['filing_status'] == 'single'][weight_col].sum()
            mfs_count = df[df['filing_status'] == 'married_filing_separately'][weight_col].sum()
            total_single_mfs = single_count + mfs_count
            
            if total_single_mfs > 0:
                if status == 'single':
                    target = SOI_BENCHMARKS['single_and_mfs']['total'] * (single_count / total_single_mfs)
                else:
                    target = SOI_BENCHMARKS['single_and_mfs']['total'] * (mfs_count / total_single_mfs)
            else:
                target = 0
        elif status == 'married_filing_jointly':
            target = SOI_BENCHMARKS['married_filing_jointly']['total']
        elif status == 'head_of_household':
            target = SOI_BENCHMARKS['head_of_household']['total']
        else:
            target = 0
        
        gap = after - target if target > 0 else 0
        gap_pct = (gap / target * 100) if target > 0 else 0
        
        logger.info(f"{status:<30} {before:>12,.0f} {after:>12,.0f} {target:>12,.0f} {gap_pct:>+9.1f}%")
    
    return df


def validate_calibration(tax_units: pd.DataFrame, weight_col: str = 'weight_calibrated') -> dict:
    """
    Validate calibration results against SOI benchmarks.
    
    Args:
        tax_units: Calibrated tax units DataFrame
        weight_col: Column name for calibrated weights
        
    Returns:
        Dictionary with validation metrics
    """
    df = calculate_taxable_income(tax_units)
    
    validation = {
        'total_units': len(df),
        'total_weight': df[weight_col].sum(),
        'filing_status_gaps': {},
        'bracket_accuracy': {}
    }
    
    # Check each filing status
    for status in ['single', 'married_filing_separately', 'married_filing_jointly', 'head_of_household']:
        status_df = df[df['filing_status'] == status]
        
        if status_df.empty:
            continue
        
        # Get SOI benchmark
        if status in ['single', 'married_filing_separately']:
            benchmark = SOI_BENCHMARKS['single_and_mfs']
            # Estimate split
            if status == 'single':
                target = benchmark['total'] * 0.95  # Rough estimate
            else:
                target = benchmark['total'] * 0.05
        elif status == 'married_filing_jointly':
            benchmark = SOI_BENCHMARKS['married_filing_jointly']
            target = benchmark['total']
        elif status == 'head_of_household':
            benchmark = SOI_BENCHMARKS['head_of_household']
            target = benchmark['total']
        else:
            continue
        
        current = status_df[weight_col].sum()
        gap = (current - target) / target if target > 0 else 0
        
        validation['filing_status_gaps'][status] = {
            'current': current,
            'target': target,
            'gap_pct': gap * 100
        }
    
    return validation
