#!/usr/bin/env python3
"""
Detailed Tax Liability Calibration Module

Calibrates PUMS tax units using DOTAX SOI 2022 Table A-9 (Selected Resident Return Data).
This provides more detailed AGI brackets and actual tax liability benchmarks compared to Table 12A.

Key Features:
- 15 detailed AGI brackets per filing status (vs 12 in Table 12A)
- Includes actual tax liability by bracket
- Covers AGI under $150,000 (573,253 returns out of ~635,000 total)
- Separate benchmarks for joint, single, and head of household filers

Data Source: DOTAX SOI 2022 Table A-9 (A9-2, A9-3, A9-4)
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Path to detailed benchmarks
BENCHMARKS_PATH = Path(__file__).parent.parent.parent.parent / 'data' / 'processed' / 'detailed_tax_liability_benchmarks.csv'


def load_detailed_benchmarks():
    """
    Load DOTAX SOI 2022 detailed tax liability benchmarks.
    
    Returns:
        DataFrame with columns:
        - filing_status: 'joint', 'single', 'hoh'
        - agi_min, agi_max: AGI bracket bounds
        - returns: Number of returns
        - total_agi_millions: Total AGI in millions
        - tax_before_credits_millions: Tax before credits
        - tax_after_credits_millions: Tax after credits
        - avg_tax_before, avg_tax_after: Average tax per return
        - effective_rate_before_pct, effective_rate_after_pct: Effective tax rates
    """
    if not BENCHMARKS_PATH.exists():
        raise FileNotFoundError(
            f"Detailed benchmarks not found at {BENCHMARKS_PATH}. "
            "Run scripts/parse_detailed_tax_liability.py first."
        )
    
    df = pd.read_csv(BENCHMARKS_PATH)
    logger.info(f"Loaded detailed benchmarks: {len(df)} brackets, {df['returns'].sum():,.0f} total returns")
    return df


def map_filing_status_to_dotax(pums_status):
    """
    Map PUMS filing status to DOTAX categories.
    
    DOTAX categories:
    - 'joint': Married Filing Jointly
    - 'single': Single and Married Filing Separately
    - 'hoh': Head of Household (includes Qualifying Widow(er))
    
    Args:
        pums_status: PUMS filing status string
        
    Returns:
        str: DOTAX category ('joint', 'single', or 'hoh')
    """
    status_map = {
        'married_filing_jointly': 'joint',
        'single': 'single',
        'married_filing_separately': 'single',  # DOTAX groups MFS with Single
        'head_of_household': 'hoh',
        'qualifying_widow': 'hoh'  # DOTAX groups QW with HoH
    }
    return status_map.get(pums_status, 'single')


def assign_detailed_agi_bracket(agi, filing_status, benchmarks_df):
    """
    Assign detailed AGI bracket for a given AGI and filing status.
    
    Args:
        agi: Adjusted Gross Income
        filing_status: DOTAX filing status ('joint', 'single', 'hoh')
        benchmarks_df: DataFrame with benchmark data
        
    Returns:
        tuple: (agi_min, agi_max) for the bracket, or None if above $150k
    """
    # Filter to relevant filing status
    status_brackets = benchmarks_df[benchmarks_df['filing_status'] == filing_status].copy()
    
    # Find matching bracket
    mask = (status_brackets['agi_min'] <= agi) & (agi < status_brackets['agi_max'])
    matches = status_brackets[mask]
    
    if len(matches) == 0:
        # Above $150k - no detailed data available
        return None
    
    return (matches.iloc[0]['agi_min'], matches.iloc[0]['agi_max'])


def apply_detailed_tax_calibration(
    tax_units,
    weight_col='weight',
    agi_col='agi',
    filing_status_col='filing_status',
    output_weight_col='weight_detailed_calibrated',
    max_iterations=100,
    tolerance=0.001
):
    """
    Apply IPF calibration using detailed DOTAX SOI benchmarks.
    
    This calibrates to:
    1. Filing status distribution (joint, single, hoh)
    2. Detailed AGI brackets within each filing status (15 brackets)
    3. Total returns
    
    Args:
        tax_units: DataFrame with tax unit data
        weight_col: Name of weight column
        agi_col: Name of AGI column
        filing_status_col: Name of filing status column
        output_weight_col: Name for calibrated weight column
        max_iterations: Maximum IPF iterations
        tolerance: Convergence tolerance
        
    Returns:
        DataFrame with calibrated weights
    """
    from lab.validation.ipf_calibration import iterative_proportional_fitting
    
    # Load benchmarks
    benchmarks = load_detailed_benchmarks()
    
    # Prepare tax units
    df = tax_units.copy()
    
    # Estimate AGI if not present
    if agi_col not in df.columns:
        if 'total_income' in df.columns:
            logger.info(f"'{agi_col}' not found, estimating from 'total_income'")
            df[agi_col] = df['total_income'] * 0.99
        elif 'income' in df.columns:
            logger.info(f"'{agi_col}' not found, estimating from 'income'")
            df[agi_col] = df['income'] * 0.99
        else:
            raise ValueError(f"Column '{agi_col}' not found and cannot be estimated")
    
    # Map filing status to DOTAX categories
    df['_dotax_status'] = df[filing_status_col].apply(map_filing_status_to_dotax)
    
    # Assign detailed AGI brackets
    logger.info("Assigning detailed AGI brackets...")
    df['_detailed_bracket'] = df.apply(
        lambda row: assign_detailed_agi_bracket(row[agi_col], row['_dotax_status'], benchmarks),
        axis=1
    )
    
    # Filter to records with valid brackets (AGI < $150k)
    valid_mask = df['_detailed_bracket'].notna()
    n_above_150k = (~valid_mask).sum()
    if n_above_150k > 0:
        logger.warning(
            f"{n_above_150k:,} tax units ({n_above_150k/len(df)*100:.1f}%) have AGI ≥ $150k "
            "and cannot be calibrated with detailed benchmarks. "
            "These will retain original weights."
        )
    
    df_calibrate = df[valid_mask].copy()
    df_above_150k = df[~valid_mask].copy()
    
    # Create composite category for IPF: filing_status + bracket
    df_calibrate['_ipf_category'] = df_calibrate.apply(
        lambda row: f"{row['_dotax_status']}_{row['_detailed_bracket']}",
        axis=1
    )
    
    # Build target totals for IPF
    targets = {}
    for _, row in benchmarks.iterrows():
        status = row['filing_status']
        bracket = (row['agi_min'], row['agi_max'])
        category = f"{status}_{bracket}"
        targets[category] = row['returns']
    
    logger.info(f"Calibrating {len(df_calibrate):,} tax units to {len(targets)} detailed brackets...")
    logger.info(f"Target total: {sum(targets.values()):,.0f} returns")
    
    # Apply IPF calibration
    df_calibrate[output_weight_col] = iterative_proportional_fitting(
        df_calibrate,
        weight_col=weight_col,
        category_col='_ipf_category',
        targets=targets,
        max_iterations=max_iterations,
        tolerance=tolerance
    )
    
    # For records above $150k, use original weights
    df_above_150k[output_weight_col] = df_above_150k[weight_col]
    
    # Combine back together
    df_final = pd.concat([df_calibrate, df_above_150k], ignore_index=False).sort_index()
    
    # Clean up temporary columns
    df_final = df_final.drop(columns=['_dotax_status', '_detailed_bracket', '_ipf_category'], errors='ignore')
    
    logger.info("✅ Detailed calibration complete")
    logger.info(f"  Total returns (calibrated): {df_final[output_weight_col].sum():,.0f}")
    logger.info(f"  Records calibrated: {len(df_calibrate):,}")
    logger.info(f"  Records with original weights: {len(df_above_150k):,}")
    
    return df_final


def validate_detailed_calibration(tax_units, weight_col='weight_detailed_calibrated', agi_col='agi', filing_status_col='filing_status'):
    """
    Validate calibration against detailed benchmarks.
    
    Args:
        tax_units: DataFrame with calibrated tax units
        weight_col: Name of calibrated weight column
        agi_col: Name of AGI column
        filing_status_col: Name of filing status column
        
    Returns:
        dict: Validation results with errors by category
    """
    benchmarks = load_detailed_benchmarks()
    df = tax_units.copy()
    
    # Map filing status
    df['_dotax_status'] = df[filing_status_col].apply(map_filing_status_to_dotax)
    
    # Assign brackets
    df['_detailed_bracket'] = df.apply(
        lambda row: assign_detailed_agi_bracket(row[agi_col], row['_dotax_status'], benchmarks),
        axis=1
    )
    
    # Calculate actual totals
    df['_category'] = df.apply(
        lambda row: f"{row['_dotax_status']}_{row['_detailed_bracket']}" if pd.notna(row['_detailed_bracket']) else None,
        axis=1
    )
    
    actual = df[df['_category'].notna()].groupby('_category')[weight_col].sum()
    
    # Compare to benchmarks
    results = []
    for _, row in benchmarks.iterrows():
        status = row['filing_status']
        bracket = (row['agi_min'], row['agi_max'])
        category = f"{status}_{bracket}"
        
        target = row['returns']
        actual_val = actual.get(category, 0)
        error = abs(actual_val - target) / target * 100 if target > 0 else 0
        
        results.append({
            'filing_status': status,
            'agi_min': row['agi_min'],
            'agi_max': row['agi_max'],
            'target': target,
            'actual': actual_val,
            'error_pct': error
        })
    
    results_df = pd.DataFrame(results)
    
    # Summary statistics
    total_target = results_df['target'].sum()
    total_actual = results_df['actual'].sum()
    total_error = abs(total_actual - total_target) / total_target * 100
    
    max_error = results_df['error_pct'].max()
    mean_error = results_df['error_pct'].mean()
    
    logger.info("\n" + "="*80)
    logger.info("DETAILED CALIBRATION VALIDATION")
    logger.info("="*80)
    logger.info(f"Total target: {total_target:,.0f}")
    logger.info(f"Total actual: {total_actual:,.0f}")
    logger.info(f"Total error: {total_error:.3f}%")
    logger.info(f"Max bracket error: {max_error:.3f}%")
    logger.info(f"Mean bracket error: {mean_error:.3f}%")
    
    # Show brackets with largest errors
    logger.info("\nTop 10 brackets by error:")
    top_errors = results_df.nlargest(10, 'error_pct')
    for _, row in top_errors.iterrows():
        logger.info(
            f"  {row['filing_status']:<8} ${row['agi_min']:>10,.0f}-${row['agi_max']:>10,.0f}: "
            f"target={row['target']:>8,.0f}, actual={row['actual']:>8,.0f}, error={row['error_pct']:>6.2f}%"
        )
    
    return {
        'results_df': results_df,
        'total_error_pct': total_error,
        'max_error_pct': max_error,
        'mean_error_pct': mean_error
    }


def get_benchmark_summary():
    """
    Get summary of detailed benchmarks.
    
    Returns:
        DataFrame with summary by filing status
    """
    benchmarks = load_detailed_benchmarks()
    
    summary = benchmarks.groupby('filing_status').agg({
        'returns': 'sum',
        'total_agi_millions': 'sum',
        'tax_after_credits_millions': 'sum'
    }).round(1)
    
    summary['avg_agi'] = (summary['total_agi_millions'] * 1e6 / summary['returns']).round(0)
    summary['avg_tax'] = (summary['tax_after_credits_millions'] * 1e6 / summary['returns']).round(0)
    summary['effective_rate'] = (summary['tax_after_credits_millions'] / summary['total_agi_millions'] * 100).round(2)
    
    return summary
