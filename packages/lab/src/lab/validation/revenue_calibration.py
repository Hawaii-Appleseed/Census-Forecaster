#!/usr/bin/env python3
"""
Revenue-Focused Calibration

Implements AGI-only calibration using 1D IPF for accurate revenue estimation.
This approach prioritizes matching AGI totals (which drive revenue) over return counts.

Key insight: Revenue = f(AGI, filing_status, ...), so matching AGI ensures revenue accuracy.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


def apply_revenue_calibration(
    tax_units: pd.DataFrame,
    benchmarks: pd.DataFrame,
    weight_col: str = 'weight',
    agi_col: str = 'agi',
    filing_status_col: str = 'filing_status',
    output_weight_col: str = 'weight_revenue_calibrated',
    max_iterations: int = 100,
    tolerance: float = 0.001
) -> pd.DataFrame:
    """
    Apply AGI-only calibration for accurate revenue estimation.
    
    Uses 1D IPF to match AGI totals by bracket and filing status.
    This ensures accurate revenue estimates without the convergence
    issues of 2D IPF.
    
    Args:
        tax_units: Tax units DataFrame
        benchmarks: Benchmark data with AGI targets
        weight_col: Input weight column
        agi_col: AGI column name
        filing_status_col: Filing status column name
        output_weight_col: Output weight column name
        max_iterations: Maximum IPF iterations
        tolerance: Convergence tolerance
        
    Returns:
        DataFrame with revenue-calibrated weights
    """
    logger.info("="*80)
    logger.info("REVENUE-FOCUSED CALIBRATION (AGI-Only 1D IPF)")
    logger.info("="*80)
    logger.info("\nStrategy: Match AGI totals to ensure accurate revenue estimates")
    logger.info("Trade-off: Return counts will be approximate (but revenue will be accurate)\n")
    
    df = tax_units.copy()
    
    # Initialize weights
    weights = df[weight_col].values.copy()
    
    # Create category column for IPF (filing_status + AGI bracket)
    df['_ipf_category'] = df.apply(
        lambda row: f"{row[filing_status_col]}_{row[agi_col]//1000}k",
        axis=1
    )
    
    # Build AGI targets from benchmarks
    targets_agi = {}
    
    for _, benchmark in benchmarks.iterrows():
        status = benchmark['filing_status']
        agi_min = benchmark['agi_min']
        agi_max = benchmark['agi_max']
        target_agi_millions = benchmark['total_agi_millions']
        
        # Map status names
        status_map = {
            'joint': 'married_filing_jointly',
            'single': 'single',
            'hoh': 'head_of_household'
        }
        
        if status not in status_map:
            continue
        
        mapped_status = status_map[status]
        
        # Find matching records
        mask = (df[filing_status_col] == mapped_status) & \
               (df[agi_col] >= agi_min) & \
               (df[agi_col] < agi_max)
        
        if mask.sum() == 0:
            continue
        
        # Create unique key for this bracket
        key = f"{mapped_status}_{agi_min}_{agi_max}"
        targets_agi[key] = target_agi_millions * 1e6  # Convert to dollars
        
        # Update category for matching records
        df.loc[mask, '_ipf_category'] = key
    
    logger.info(f"Calibrating {len(df):,} tax units to {len(targets_agi)} AGI targets...")
    logger.info(f"Target total AGI: ${sum(targets_agi.values())/1e9:.2f}B\n")
    
    # Run 1D IPF on AGI totals
    for iteration in range(max_iterations):
        max_error = 0
        
        for category, target_agi in targets_agi.items():
            mask = df['_ipf_category'] == category
            
            if mask.sum() == 0:
                continue
            
            # Calculate current AGI total
            current_agi = (weights[mask] * df.loc[mask, agi_col]).sum()
            
            if current_agi <= 0:
                continue
            
            # Calculate adjustment factor
            factor = target_agi / current_agi
            
            # Apply adjustment
            weights[mask] *= factor
            
            # Track error
            error = abs(factor - 1.0)
            max_error = max(max_error, error)
        
        # Check convergence
        if max_error < tolerance:
            logger.info(f"✅ Converged after {iteration + 1} iterations (max error: {max_error:.4f})")
            break
        
        # Log progress every 20 iterations
        if (iteration + 1) % 20 == 0:
            logger.info(f"   Iteration {iteration + 1}: max error = {max_error:.4f}")
    else:
        logger.warning(f"⚠️  Did not converge after {max_iterations} iterations (max error: {max_error:.4f})")
    
    # Store calibrated weights
    df[output_weight_col] = weights
    
    # Validation
    logger.info("\n" + "="*80)
    logger.info("CALIBRATION VALIDATION")
    logger.info("="*80)
    
    total_target_agi = sum(targets_agi.values())
    total_actual_agi = (df[output_weight_col] * df[agi_col]).sum()
    overall_error = abs(total_actual_agi - total_target_agi) / total_target_agi * 100
    
    logger.info(f"\nOverall AGI:")
    logger.info(f"  Target: ${total_target_agi/1e9:.2f}B")
    logger.info(f"  Actual: ${total_actual_agi/1e9:.2f}B")
    logger.info(f"  Error: {overall_error:.2f}%")
    
    if overall_error < 1:
        logger.info(f"\n✅ Excellent accuracy - Revenue estimates will be highly accurate")
    elif overall_error < 5:
        logger.info(f"\n✅ Good accuracy - Revenue estimates will be accurate")
    else:
        logger.info(f"\n⚠️  Error > 5% - Review calibration")
    
    # Sample bracket validation
    logger.info("\n" + "="*80)
    logger.info("SAMPLE BRACKET VALIDATION")
    logger.info("="*80)
    
    test_brackets = [
        ('married_filing_jointly', 150000, 200000),
        ('married_filing_jointly', 200000, 300000),
        ('single', 150000, 200000),
        ('head_of_household', 150000, 200000),
    ]
    
    for status, agi_min, agi_max in test_brackets:
        key = f"{status}_{agi_min}_{agi_max}"
        
        if key not in targets_agi:
            continue
        
        mask = (df[filing_status_col] == status) & \
               (df[agi_col] >= agi_min) & \
               (df[agi_col] < agi_max)
        
        target_agi = targets_agi[key]
        actual_agi = (df[mask][output_weight_col] * df[mask][agi_col]).sum()
        error = abs(actual_agi - target_agi) / target_agi * 100 if target_agi > 0 else 0
        
        status_short = status.replace('married_filing_jointly', 'joint').replace('head_of_household', 'hoh')
        logger.info(f"\n{status_short.upper()} ${agi_min//1000}k-${agi_max//1000}k:")
        logger.info(f"  Target AGI: ${target_agi/1e6:.1f}M")
        logger.info(f"  Actual AGI: ${actual_agi/1e6:.1f}M")
        logger.info(f"  Error: {error:.2f}%")
        
        if error < 1:
            logger.info(f"  ✅ Excellent")
        elif error < 5:
            logger.info(f"  ✅ Good")
        else:
            logger.info(f"  ⚠️  Needs review")
    
    # Clean up temporary columns
    df = df.drop(columns=['_ipf_category'])
    
    logger.info("\n" + "="*80)
    logger.info("REVENUE CALIBRATION COMPLETE")
    logger.info("="*80)
    logger.info(f"\nUse '{output_weight_col}' for revenue calculations")
    logger.info("Note: Return counts are approximate, but AGI (and thus revenue) is accurate\n")
    
    return df


def validate_revenue_accuracy(
    tax_units: pd.DataFrame,
    benchmarks: pd.DataFrame,
    weight_col: str = 'weight_revenue_calibrated',
    agi_col: str = 'agi',
    filing_status_col: str = 'filing_status'
) -> pd.DataFrame:
    """
    Validate revenue calibration accuracy.
    
    Returns DataFrame with error metrics by bracket.
    """
    results = []
    
    for _, benchmark in benchmarks.iterrows():
        status = benchmark['filing_status']
        agi_min = benchmark['agi_min']
        agi_max = benchmark['agi_max']
        target_agi = benchmark['total_agi_millions'] * 1e6
        
        # Map status
        status_map = {
            'joint': 'married_filing_jointly',
            'single': 'single',
            'hoh': 'head_of_household'
        }
        
        if status not in status_map:
            continue
        
        mapped_status = status_map[status]
        
        # Get actual
        mask = (tax_units[filing_status_col] == mapped_status) & \
               (tax_units[agi_col] >= agi_min) & \
               (tax_units[agi_col] < agi_max)
        
        if mask.sum() == 0:
            continue
        
        actual_agi = (tax_units[mask][weight_col] * tax_units[mask][agi_col]).sum()
        error_pct = abs(actual_agi - target_agi) / target_agi * 100 if target_agi > 0 else 0
        
        results.append({
            'filing_status': status,
            'agi_min': agi_min,
            'agi_max': agi_max,
            'target_agi_millions': target_agi / 1e6,
            'actual_agi_millions': actual_agi / 1e6,
            'error_pct': error_pct,
            'sample_size': mask.sum()
        })
    
    return pd.DataFrame(results)
