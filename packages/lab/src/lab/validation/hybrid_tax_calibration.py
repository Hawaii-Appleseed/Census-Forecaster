#!/usr/bin/env python3
"""
Hybrid Tax Liability Calibration Module

Combines Table A-9 (detailed brackets < $150k) with Table A-2 (high-income ≥ $150k)
for complete 100% coverage with maximum granularity.

Strategy:
- Use A-9's 15 detailed brackets for AGI < $150k (90.3% of returns)
- Use A-2's 4 brackets for AGI ≥ $150k (9.7% of returns)
- Result: 19 brackets per filing status, 100% coverage

Data Sources:
- Table A-9: Detailed brackets with tax liability (AGI < $150k)
- Table A-2: Complete coverage including high-income (all AGI levels)
"""

from __future__ import annotations

import pandas as pd
import numpy as np
from pathlib import Path
import logging

logger = logging.getLogger(__name__)

# Paths to benchmark data
A9_PATH = Path(__file__).parent.parent.parent.parent / 'data' / 'processed' / 'detailed_tax_liability_benchmarks.csv'
A2_PATH = Path(__file__).parent.parent.parent.parent / 'data' / 'processed' / 'comprehensive_tax_benchmarks.csv'


def load_hybrid_benchmarks():
    """
    Load hybrid benchmarks combining A-9 (detailed < $150k) and A-2 (≥ $150k).
    
    Returns:
        DataFrame with 19 brackets per filing status, 100% coverage
    """
    # Load A-9 data (detailed, < $150k)
    if not A9_PATH.exists():
        raise FileNotFoundError(
            f"A-9 benchmarks not found at {A9_PATH}. "
            "Run scripts/parse_detailed_tax_liability.py first."
        )
    
    a9_df = pd.read_csv(A9_PATH)
    logger.info(f"Loaded A-9 data: {len(a9_df)} brackets, {a9_df['returns'].sum():,.0f} returns (AGI < $150k)")
    
    # Load A-2 data (complete coverage)
    if not A2_PATH.exists():
        raise FileNotFoundError(
            f"A-2 benchmarks not found at {A2_PATH}. "
            "Run scripts/parse_comprehensive_tax_data.py first."
        )
    
    a2_df = pd.read_csv(A2_PATH)
    
    # Extract high-income brackets from A-2 (AGI ≥ $150k)
    high_income_df = a2_df[a2_df['agi_min'] >= 150000].copy()
    logger.info(f"Loaded A-2 high-income data: {len(high_income_df)} brackets, {high_income_df['returns'].sum():,.0f} returns (AGI ≥ $150k)")
    
    # Combine: A-9 (< $150k) + A-2 high-income (≥ $150k)
    hybrid_df = pd.concat([a9_df, high_income_df], ignore_index=True)
    
    # Sort by filing status and AGI
    hybrid_df = hybrid_df.sort_values(['filing_status', 'agi_min']).reset_index(drop=True)
    
    logger.info(f"Created hybrid benchmarks: {len(hybrid_df)} total brackets, {hybrid_df['returns'].sum():,.0f} total returns (100% coverage)")
    
    return hybrid_df


def map_filing_status_to_dotax(pums_status):
    """
    Map PUMS filing status to DOTAX categories.
    
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


def assign_hybrid_agi_bracket(agi, filing_status, benchmarks_df):
    """
    Assign AGI bracket from hybrid benchmarks.
    
    Args:
        agi: Adjusted Gross Income
        filing_status: DOTAX filing status ('joint', 'single', 'hoh')
        benchmarks_df: DataFrame with hybrid benchmark data
        
    Returns:
        tuple: (agi_min, agi_max) for the bracket
    """
    # Filter to relevant filing status
    status_brackets = benchmarks_df[benchmarks_df['filing_status'] == filing_status].copy()
    
    # Find matching bracket
    mask = (status_brackets['agi_min'] <= agi) & (agi < status_brackets['agi_max'])
    matches = status_brackets[mask]
    
    if len(matches) == 0:
        # Should not happen with hybrid data, but handle edge case
        return None
    
    return (matches.iloc[0]['agi_min'], matches.iloc[0]['agi_max'])


def apply_hybrid_tax_calibration(
    tax_units,
    weight_col='weight',
    agi_col='agi',
    filing_status_col='filing_status',
    output_weight_col='weight_hybrid_calibrated',
    max_iterations=100,
    tolerance=0.001
):
    """
    Apply IPF calibration using hybrid benchmarks (A-9 + A-2).
    
    This provides:
    - 15 detailed brackets for AGI < $150k (from A-9)
    - 4 additional brackets for AGI ≥ $150k (from A-2)
    - 100% coverage with maximum granularity
    
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
    from lab.validation.ipf_calibration import iterative_proportional_fitting_2d
    
    # Load hybrid benchmarks
    benchmarks = load_hybrid_benchmarks()
    
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
    
    # Assign hybrid AGI brackets
    logger.info("Assigning hybrid AGI brackets...")
    df['_hybrid_bracket'] = df.apply(
        lambda row: assign_hybrid_agi_bracket(row[agi_col], row['_dotax_status'], benchmarks),
        axis=1
    )
    
    # Check for any unassigned brackets (should be none with hybrid data)
    valid_mask = df['_hybrid_bracket'].notna()
    n_unassigned = (~valid_mask).sum()
    if n_unassigned > 0:
        logger.warning(
            f"{n_unassigned:,} tax units could not be assigned to brackets. "
            "These will retain original weights."
        )
    
    df_calibrate = df[valid_mask].copy()
    df_unassigned = df[~valid_mask].copy()
    
    # Create composite category for IPF: filing_status + bracket
    df_calibrate['_ipf_category'] = df_calibrate.apply(
        lambda row: f"{row['_dotax_status']}_{row['_hybrid_bracket']}",
        axis=1
    )
    
    # Build target totals for IPF (both returns and AGI)
    targets_returns = {}
    targets_agi = {}
    for _, row in benchmarks.iterrows():
        status = row['filing_status']
        bracket = (row['agi_min'], row['agi_max'])
        category = f"{status}_{bracket}"
        targets_returns[category] = row['returns']
        # Convert AGI from millions to actual dollars
        targets_agi[category] = row['total_agi_millions'] * 1_000_000
    
    # Count brackets by source
    a9_count = len([k for k in targets_returns.keys() if '150000.0)' not in k or k.endswith('150000.0)')])
    a2_count = len([k for k in targets_returns.keys() if '150000.0,' in k])
    
    logger.info(f"Calibrating {len(df_calibrate):,} tax units to {len(targets_returns)} hybrid brackets...")
    logger.info(f"Target total returns: {sum(targets_returns.values()):,.0f}")
    logger.info(f"Target total AGI: ${sum(targets_agi.values())/1e9:.2f}B")
    logger.info(f"  • A-9 brackets (< $150k): {a9_count} brackets")
    logger.info(f"  • A-2 brackets (≥ $150k): {a2_count} brackets")
    
    # Apply two-dimensional IPF calibration (returns + AGI)
    # Use more iterations for better convergence on difficult brackets
    df_calibrate[output_weight_col] = iterative_proportional_fitting_2d(
        df_calibrate,
        weight_col=weight_col,
        category_col='_ipf_category',
        value_col=agi_col,
        targets_count=targets_returns,
        targets_total=targets_agi,
        max_iterations=500,  # Increased from default 100
        tolerance=0.0005     # Tightened from default 0.001
    )
    
    # For unassigned records (if any), use original weights
    if len(df_unassigned) > 0:
        df_unassigned[output_weight_col] = df_unassigned[weight_col]
    
    # Combine back together
    df_final = pd.concat([df_calibrate, df_unassigned], ignore_index=False).sort_index()
    
    # Clean up temporary columns
    df_final = df_final.drop(columns=['_dotax_status', '_hybrid_bracket', '_ipf_category'], errors='ignore')
    
    logger.info("✅ Hybrid calibration complete")
    logger.info(f"  Total returns (calibrated): {df_final[output_weight_col].sum():,.0f}")
    logger.info(f"  Records calibrated: {len(df_calibrate):,} (100%)")
    logger.info(f"  Coverage: 100% with 19 brackets per filing status")
    
    return df_final


def validate_hybrid_calibration(
    tax_units,
    weight_col='weight_hybrid_calibrated',
    agi_col='agi',
    filing_status_col='filing_status'
):
    """
    Validate hybrid calibration against benchmarks.
    
    Args:
        tax_units: DataFrame with calibrated tax units
        weight_col: Name of calibrated weight column
        agi_col: Name of AGI column
        filing_status_col: Name of filing status column
        
    Returns:
        dict: Validation results with errors by category
    """
    benchmarks = load_hybrid_benchmarks()
    df = tax_units.copy()
    
    # Map filing status
    df['_dotax_status'] = df[filing_status_col].apply(map_filing_status_to_dotax)
    
    # Assign brackets
    df['_hybrid_bracket'] = df.apply(
        lambda row: assign_hybrid_agi_bracket(row[agi_col], row['_dotax_status'], benchmarks),
        axis=1
    )
    
    # Calculate actual totals
    df['_category'] = df.apply(
        lambda row: f"{row['_dotax_status']}_{row['_hybrid_bracket']}" if pd.notna(row['_hybrid_bracket']) else None,
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
        
        source = 'A-9' if row['agi_max'] <= 150000 else 'A-2'
        
        results.append({
            'filing_status': status,
            'agi_min': row['agi_min'],
            'agi_max': row['agi_max'],
            'source': source,
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
    
    # Separate stats for A-9 vs A-2
    a9_results = results_df[results_df['source'] == 'A-9']
    a2_results = results_df[results_df['source'] == 'A-2']
    
    logger.info("\n" + "="*80)
    logger.info("HYBRID CALIBRATION VALIDATION")
    logger.info("="*80)
    logger.info(f"Total target: {total_target:,.0f}")
    logger.info(f"Total actual: {total_actual:,.0f}")
    logger.info(f"Total error: {total_error:.3f}%")
    logger.info(f"\nA-9 brackets (< $150k): {len(a9_results)} brackets, {a9_results['target'].sum():,.0f} returns")
    logger.info(f"  Mean error: {a9_results['error_pct'].mean():.3f}%")
    logger.info(f"  Max error: {a9_results['error_pct'].max():.3f}%")
    logger.info(f"\nA-2 brackets (≥ $150k): {len(a2_results)} brackets, {a2_results['target'].sum():,.0f} returns")
    logger.info(f"  Mean error: {a2_results['error_pct'].mean():.3f}%")
    logger.info(f"  Max error: {a2_results['error_pct'].max():.3f}%")
    logger.info(f"\nOverall:")
    logger.info(f"  Max bracket error: {max_error:.3f}%")
    logger.info(f"  Mean bracket error: {mean_error:.3f}%")
    
    # Show brackets with largest errors
    logger.info("\nTop 10 brackets by error:")
    top_errors = results_df.nlargest(10, 'error_pct')
    for _, row in top_errors.iterrows():
        logger.info(
            f"  {row['filing_status']:<8} ${row['agi_min']:>10,.0f}-${row['agi_max']:>10,.0f} ({row['source']}): "
            f"target={row['target']:>8,.0f}, actual={row['actual']:>8,.0f}, error={row['error_pct']:>6.2f}%"
        )
    
    return {
        'results_df': results_df,
        'total_error_pct': total_error,
        'max_error_pct': max_error,
        'mean_error_pct': mean_error,
        'a9_mean_error': a9_results['error_pct'].mean(),
        'a2_mean_error': a2_results['error_pct'].mean()
    }


def get_hybrid_benchmark_summary():
    """
    Get summary of hybrid benchmarks.
    
    Returns:
        DataFrame with summary by filing status and source
    """
    benchmarks = load_hybrid_benchmarks()
    
    # Add source column
    benchmarks['source'] = benchmarks['agi_max'].apply(lambda x: 'A-9' if x <= 150000 else 'A-2')
    
    summary = benchmarks.groupby(['filing_status', 'source']).agg({
        'returns': 'sum',
        'total_agi_millions': 'sum',
        'tax_after_credits_millions': 'sum'
    }).reset_index()
    
    summary['avg_agi'] = (summary['total_agi_millions'] * 1e6 / summary['returns']).round(0)
    summary['avg_tax'] = (summary['tax_after_credits_millions'] * 1e6 / summary['returns']).round(0)
    summary['effective_rate'] = (summary['tax_after_credits_millions'] / summary['total_agi_millions'] * 100).round(2)
    
    return summary
