"""
AGI-Based Calibration Module

This module implements a second calibration layer based on AGI brackets
from DOTAX Table 12A. It works alongside the existing taxable income calibration
to ensure accurate representation across the full income distribution.

Key difference from bracket_calibration.py:
- bracket_calibration.py: Uses TAXABLE INCOME brackets (for filing status-specific calibration)
- agi_calibration.py: Uses AGI brackets (for overall income distribution matching Table 12A)
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)


# DOTAX SOI 2022 Table 12A - Returns by AGI Bracket
AGI_BENCHMARKS = [
    (0, 10000, 129376),
    (10000, 20000, 64160),
    (20000, 30000, 57835),
    (30000, 40000, 59827),
    (40000, 50000, 53555),
    (50000, 75000, 91459),
    (75000, 100000, 54976),
    (100000, 150000, 62065),
    (150000, 200000, 27976),
    (200000, 300000, 18937),
    (300000, 400000, 6076),
    (400000, float('inf'), 8875),
]

TOTAL_RETURNS = 635117


def assign_agi_bracket(agi: float) -> int:
    """
    Assign AGI to a bracket index.
    
    Args:
        agi: Adjusted Gross Income
        
    Returns:
        Bracket index (0-based)
    """
    for i, (min_agi, max_agi, _) in enumerate(AGI_BENCHMARKS):
        if max_agi == float('inf'):
            if agi >= min_agi:
                return i
        else:
            if min_agi <= agi < max_agi:
                return i
    return 0  # Default to first bracket


def calculate_agi_calibration_factors(tax_units: pd.DataFrame,
                                      weight_col: str = 'weight') -> Dict[int, float]:
    """
    Calculate calibration factors for each AGI bracket.
    
    Args:
        tax_units: DataFrame with tax units
        weight_col: Column name for weights
        
    Returns:
        Dictionary mapping bracket_index -> calibration_factor
    """
    # Assign brackets
    tax_units = tax_units.copy()
    tax_units['agi_bracket'] = tax_units['agi'].apply(assign_agi_bracket)
    
    # Calculate current weighted counts by bracket
    current_counts = tax_units.groupby('agi_bracket')[weight_col].sum()
    
    # Calculate calibration factors
    factors = {}
    for i, (min_agi, max_agi, target_count) in enumerate(AGI_BENCHMARKS):
        current = current_counts.get(i, 0)
        
        if current > 0 and target_count > 0:
            factor = target_count / current
        else:
            factor = 1.0
        
        factors[i] = factor
        
        # Log significant adjustments
        if max_agi == float('inf'):
            label = f"${min_agi//1000}k+"
        else:
            label = f"${min_agi//1000}k-${max_agi//1000}k"
        
        marker = " ⚠️" if abs(factor - 1.0) > 0.15 else ""
        logger.debug(f"  {label:<15}: factor={factor:.3f} (current={current:,.0f}, target={target_count:,}){marker}")
    
    return factors


def apply_agi_calibration(tax_units: pd.DataFrame,
                          weight_col: str = 'weight',
                          output_col: str = 'weight_agi_calibrated') -> pd.DataFrame:
    """
    Apply AGI bracket calibration to tax units.
    
    This is a second-layer calibration that adjusts weights to match
    the AGI distribution from DOTAX Table 12A.
    
    Args:
        tax_units: DataFrame with tax units
        weight_col: Column name for input weights
        output_col: Column name for output calibrated weights
        
    Returns:
        DataFrame with calibrated weights
    """
    logger.info("="*80)
    logger.info("AGI BRACKET CALIBRATION (DOTAX Table 12A)")
    logger.info("="*80)
    
    df = tax_units.copy()
    
    # Store original weights if not already stored
    if 'weight_original' not in df.columns:
        df['weight_original'] = df[weight_col].copy()
    
    # Assign AGI brackets
    df['agi_bracket_idx'] = df['agi'].apply(assign_agi_bracket)
    
    # Calculate calibration factors
    logger.info(f"\nCalculating AGI calibration factors...")
    factors = calculate_agi_calibration_factors(df, weight_col)
    
    # Apply factors
    df['agi_calibration_factor'] = df['agi_bracket_idx'].map(factors)
    df[output_col] = df[weight_col] * df['agi_calibration_factor']
    
    # Report results
    logger.info(f"\nAGI Bracket Calibration Results:")
    logger.info(f"{'Bracket':<15} {'Before':>12} {'After':>12} {'Target':>12} {'Factor':>8}")
    logger.info("-"*65)
    
    for i, (min_agi, max_agi, target_count) in enumerate(AGI_BENCHMARKS):
        if max_agi == float('inf'):
            label = f"${min_agi//1000}k+"
        else:
            label = f"${min_agi//1000}k-${max_agi//1000}k"
        
        bracket_df = df[df['agi_bracket_idx'] == i]
        before = bracket_df[weight_col].sum()
        after = bracket_df[output_col].sum()
        factor = factors[i]
        
        logger.info(f"{label:<15} {before:>12,.0f} {after:>12,.0f} {target_count:>12,} {factor:>7.3f}x")
    
    # Summary
    total_before = df[weight_col].sum()
    total_after = df[output_col].sum()
    
    logger.info("-"*65)
    logger.info(f"{'TOTAL':<15} {total_before:>12,.0f} {total_after:>12,.0f} {TOTAL_RETURNS:>12,}")
    logger.info(f"\nTotal change: {total_after - total_before:+,.0f} ({(total_after - total_before)/total_before*100:+.1f}%)")
    
    return df


def apply_two_layer_calibration(tax_units: pd.DataFrame,
                                weight_col: str = 'weight') -> pd.DataFrame:
    """
    Apply two-layer calibration:
    1. Filing status + taxable income brackets (existing)
    2. AGI brackets (new)
    
    Args:
        tax_units: DataFrame with tax units
        weight_col: Column name for initial weights
        
    Returns:
        DataFrame with both calibration layers applied
    """
    from tax_modeler.units.status.bracket_calibration import apply_bracket_calibration
    
    logger.info("\n" + "="*80)
    logger.info("TWO-LAYER CALIBRATION APPROACH")
    logger.info("="*80)
    logger.info("\nLayer 1: Filing Status + Taxable Income Brackets")
    logger.info("Layer 2: AGI Bracket Distribution")
    
    # Layer 1: Filing status calibration (existing)
    df = apply_bracket_calibration(tax_units, weight_col=weight_col, method='factor')
    
    # Use calibrated weights as input for layer 2
    if 'weight_calibrated' in df.columns:
        layer1_weight = 'weight_calibrated'
    else:
        layer1_weight = weight_col
    
    # Layer 2: AGI calibration (new)
    df = apply_agi_calibration(df, weight_col=layer1_weight, output_col='weight_final')
    
    # Final summary
    logger.info("\n" + "="*80)
    logger.info("FINAL CALIBRATION SUMMARY")
    logger.info("="*80)
    
    original_weight = df[weight_col].sum()
    layer1_weight_total = df[layer1_weight].sum()
    final_weight = df['weight_final'].sum()
    
    logger.info(f"\nOriginal weight:        {original_weight:>12,.0f}")
    logger.info(f"After Layer 1:          {layer1_weight_total:>12,.0f} ({(layer1_weight_total - original_weight)/original_weight*100:+.1f}%)")
    logger.info(f"After Layer 2 (Final):  {final_weight:>12,.0f} ({(final_weight - layer1_weight_total)/layer1_weight_total*100:+.1f}%)")
    logger.info(f"Total change:           {final_weight - original_weight:>+12,.0f} ({(final_weight - original_weight)/original_weight*100:+.1f}%)")
    logger.info(f"\nTarget (DOTAX):         {TOTAL_RETURNS:>12,}")
    logger.info(f"Final vs Target:        {final_weight - TOTAL_RETURNS:>+12,.0f} ({(final_weight - TOTAL_RETURNS)/TOTAL_RETURNS*100:+.2f}%)")
    
    return df


def validate_agi_calibration(tax_units: pd.DataFrame,
                             weight_col: str = 'weight_final') -> Dict:
    """
    Validate AGI calibration results.
    
    Args:
        tax_units: Calibrated tax units
        weight_col: Column name for calibrated weights
        
    Returns:
        Dictionary with validation metrics
    """
    df = tax_units.copy()
    df['agi_bracket_idx'] = df['agi'].apply(assign_agi_bracket)
    
    validation = {
        'total_returns': df[weight_col].sum(),
        'target_returns': TOTAL_RETURNS,
        'accuracy': (df[weight_col].sum() / TOTAL_RETURNS) * 100,
        'brackets': []
    }
    
    for i, (min_agi, max_agi, target_count) in enumerate(AGI_BENCHMARKS):
        bracket_df = df[df['agi_bracket_idx'] == i]
        current = bracket_df[weight_col].sum()
        gap_pct = ((current - target_count) / target_count * 100) if target_count > 0 else 0
        
        if max_agi == float('inf'):
            label = f"${min_agi//1000}k+"
        else:
            label = f"${min_agi//1000}k-${max_agi//1000}k"
        
        validation['brackets'].append({
            'label': label,
            'current': current,
            'target': target_count,
            'gap_pct': gap_pct,
            'accurate': abs(gap_pct) < 5  # Within 5%
        })
    
    return validation
