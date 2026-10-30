"""
IRS SOI-Based Calibration with Iterative Proportional Fitting (IPF)

This module implements calibration using IPF (raking) to match both:
1. Filing status distribution (DOTAX targets)
2. AGI bracket distribution (DOTAX Table 12A)

IPF iteratively adjusts weights to satisfy multiple marginal constraints,
finding a balanced solution that minimizes total deviation from both targets.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple

logger = logging.getLogger(__name__)

# DOTAX Filing Status Totals (from DOTAX Table 1)
DOTAX_FILING_STATUS = {
    'single': 335198,
    'married_filing_jointly': 216358,
    'head_of_household': 67393,
    'married_filing_separately': 16007,
}

# DOTAX AGI Bracket Targets (from Table 12A)
DOTAX_AGI_BRACKETS = {
    (0, 10000): 129376,
    (10000, 20000): 64160,
    (20000, 30000): 57835,
    (30000, 40000): 59827,
    (40000, 50000): 53555,
    (50000, 75000): 91459,
    (75000, 100000): 54976,
    (100000, 150000): 62065,
    (150000, 200000): 27976,
    (200000, 300000): 18937,
    (300000, 400000): 6076,
    (400000, float('inf')): 8875,
}


def assign_agi_bracket(agi: float) -> Tuple[float, float]:
    """
    Assign AGI to DOTAX bracket.
    
    Args:
        agi: Adjusted Gross Income
        
    Returns:
        Tuple of (min_agi, max_agi) for the bracket
    """
    for (min_agi, max_agi) in DOTAX_AGI_BRACKETS.keys():
        if max_agi == float('inf'):
            if agi >= min_agi:
                return (min_agi, max_agi)
        else:
            if min_agi <= agi < max_agi:
                return (min_agi, max_agi)
    return (0, 10000)  # Default


def apply_irs_soi_calibration(
    tax_units: pd.DataFrame,
    weight_col: str = 'weight',
    output_col: str = 'weight_irs_calibrated',
    max_iterations: int = 50,
    convergence_threshold: float = 0.01
) -> pd.DataFrame:
    """
    Apply IPF calibration to match both filing status and AGI bracket targets.
    
    This uses Iterative Proportional Fitting (IPF) to adjust weights to match:
    1. Filing status distribution (DOTAX targets)
    2. AGI bracket distribution (DOTAX Table 12A)
    
    Args:
        tax_units: DataFrame with tax units
        weight_col: Column name for input weights
        output_col: Column name for output calibrated weights
        max_iterations: Maximum number of IPF iterations
        convergence_threshold: Convergence criterion (max % deviation)
        
    Returns:
        DataFrame with IPF-calibrated weights
    """
    logger.info("="*80)
    logger.info("APPLYING IPF CALIBRATION (Filing Status + AGI Brackets)")
    logger.info("="*80)
    logger.info(f"\nMax iterations: {max_iterations}")
    logger.info(f"Convergence threshold: {convergence_threshold}%")
    
    df = tax_units.copy()
    
    # Store original weights
    if 'weight_original' not in df.columns:
        df['weight_original'] = df[weight_col].copy()
    
    # Initialize working weight
    df[output_col] = df[weight_col].copy()
    
    # Estimate AGI if not present (use income/total_income as proxy)
    if 'agi' not in df.columns:
        if 'total_income' in df.columns:
            df['agi'] = df['total_income'] * 0.99  # Rough estimate: AGI ≈ 99% of total income
        elif 'income' in df.columns:
            df['agi'] = df['income'] * 0.99  # Rough estimate: AGI ≈ 99% of income
        else:
            raise ValueError("DataFrame must have 'agi', 'total_income', or 'income' column")
    
    # Assign AGI brackets
    df['agi_bracket'] = df['agi'].apply(assign_agi_bracket)
    
    # IPF iteration
    for iteration in range(max_iterations):
        logger.debug(f"\n--- Iteration {iteration + 1} ---")
        
        # STEP 1: Adjust for filing status constraints
        for status, target in DOTAX_FILING_STATUS.items():
            mask = df['filing_status'] == status
            current = df.loc[mask, output_col].sum()
            
            if current > 0:
                factor = target / current
                df.loc[mask, output_col] *= factor
                logger.debug(f"  Filing status '{status}': factor={factor:.4f}")
        
        # STEP 2: Adjust for AGI bracket constraints
        for bracket_key, target in DOTAX_AGI_BRACKETS.items():
            mask = df['agi_bracket'] == bracket_key
            current = df.loc[mask, output_col].sum()
            
            if current > 0:
                factor = target / current
                df.loc[mask, output_col] *= factor
                
                min_agi, max_agi = bracket_key
                if max_agi == float('inf'):
                    label = f"${min_agi//1000}k+"
                else:
                    label = f"${min_agi//1000}k-${max_agi//1000}k"
                logger.debug(f"  AGI bracket {label}: factor={factor:.4f}")
        
        # Check convergence
        filing_status_gaps = []
        for status, target in DOTAX_FILING_STATUS.items():
            current = df[df['filing_status'] == status][output_col].sum()
            gap_pct = abs((current - target) / target * 100)
            filing_status_gaps.append(gap_pct)
        
        agi_bracket_gaps = []
        for bracket_key, target in DOTAX_AGI_BRACKETS.items():
            current = df[df['agi_bracket'] == bracket_key][output_col].sum()
            gap_pct = abs((current - target) / target * 100)
            agi_bracket_gaps.append(gap_pct)
        
        max_filing_gap = max(filing_status_gaps)
        max_agi_gap = max(agi_bracket_gaps)
        
        logger.debug(f"  Filing status: max gap={max_filing_gap:.3f}%, avg gap={np.mean(filing_status_gaps):.3f}%")
        logger.debug(f"  AGI brackets:  max gap={max_agi_gap:.3f}%, avg gap={np.mean(agi_bracket_gaps):.3f}%")
        
        # Check convergence
        if max_filing_gap < convergence_threshold and max_agi_gap < convergence_threshold:
            logger.info(f"\n✅ Converged after {iteration + 1} iterations!")
            logger.info(f"   Max filing status gap: {max_filing_gap:.3f}%")
            logger.info(f"   Max AGI bracket gap: {max_agi_gap:.3f}%")
            break
    else:
        logger.warning(f"\n⚠️  Did not fully converge after {max_iterations} iterations")
        logger.warning(f"   Max filing status gap: {max_filing_gap:.3f}%")
        logger.warning(f"   Max AGI bracket gap: {max_agi_gap:.3f}%")
    
    # Report final results
    logger.info("\n" + "="*80)
    logger.info("FILING STATUS RESULTS")
    logger.info("="*80)
    logger.info(f"\n{'Status':<30} {'Before':>12} {'After':>12} {'Target':>12} {'Gap':>10}")
    logger.info("-"*80)
    
    for status, target in DOTAX_FILING_STATUS.items():
        before = df[df['filing_status'] == status][weight_col].sum()
        after = df[df['filing_status'] == status][output_col].sum()
        gap_pct = abs((after - target) / target * 100)
        marker = " ✅" if gap_pct < 1.0 else " ⚠️" if gap_pct > 5.0 else ""
        logger.info(f"{status:<30} {before:>12,.0f} {after:>12,.0f} {target:>12,} {gap_pct:>9.2f}%{marker}")
    
    logger.info("\n" + "="*80)
    logger.info("AGI BRACKET RESULTS")
    logger.info("="*80)
    logger.info(f"\n{'Bracket':<20} {'Before':>12} {'After':>12} {'Target':>12} {'Gap':>10}")
    logger.info("-"*80)
    
    for bracket_key, target in DOTAX_AGI_BRACKETS.items():
        min_agi, max_agi = bracket_key
        if max_agi == float('inf'):
            label = f"${min_agi//1000:,}k+"
        else:
            label = f"${min_agi//1000:,}k-${max_agi//1000:,}k"
        
        before = df[df['agi_bracket'] == bracket_key][weight_col].sum()
        after = df[df['agi_bracket'] == bracket_key][output_col].sum()
        gap_pct = abs((after - target) / target * 100)
        marker = " ✅" if gap_pct < 1.0 else " ⚠️" if gap_pct > 5.0 else ""
        logger.info(f"{label:<20} {before:>12,.0f} {after:>12,.0f} {target:>12,} {gap_pct:>9.2f}%{marker}")
    
    return df


def validate_irs_soi_calibration(
    tax_units: pd.DataFrame,
    weight_col: str = 'weight_irs_calibrated'
) -> Dict:
    """
    Validate IRS SOI calibration results.
    
    Args:
        tax_units: Calibrated tax units
        weight_col: Column name for calibrated weights
        
    Returns:
        Dictionary with validation metrics
    """
    df = tax_units.copy()
    
    # Ensure AGI bracket is assigned
    if 'agi_bracket' not in df.columns:
        df['agi_bracket'] = df['agi'].apply(assign_agi_bracket)
    
    # Calculate filing status distribution
    filing_status_sums = df.groupby('filing_status')[weight_col].sum()
    
    # Calculate AGI bracket distribution
    agi_bracket_sums = df.groupby('agi_bracket')[weight_col].sum()
    
    # Calculate gaps from targets
    filing_status_gaps = {}
    for status, target in DOTAX_FILING_STATUS.items():
        current = filing_status_sums.get(status, 0)
        gap_pct = abs((current - target) / target * 100) if target > 0 else 0
        filing_status_gaps[status] = gap_pct
    
    agi_bracket_gaps = {}
    for bracket, target in DOTAX_AGI_BRACKETS.items():
        current = agi_bracket_sums.get(bracket, 0)
        gap_pct = abs((current - target) / target * 100) if target > 0 else 0
        agi_bracket_gaps[bracket] = gap_pct
    
    # Calculate overall gap metrics
    max_filing_status_gap = max(filing_status_gaps.values()) if filing_status_gaps else 0
    max_agi_bracket_gap = max(agi_bracket_gaps.values()) if agi_bracket_gaps else 0
    
    # Count how many targets are within 1% and 5%
    filing_status_within_1pct = sum(1 for gap in filing_status_gaps.values() if gap <= 1.0)
    filing_status_within_5pct = sum(1 for gap in filing_status_gaps.values() if gap <= 5.0)
    
    agi_bracket_within_1pct = sum(1 for gap in agi_bracket_gaps.values() if gap <= 1.0)
    agi_bracket_within_5pct = sum(1 for gap in agi_bracket_gaps.values() if gap <= 5.0)
    
    return {
        'total_returns': df[weight_col].sum(),
        'filing_status': {
            'current': filing_status_sums.to_dict(),
            'target': DOTAX_FILING_STATUS,
            'gaps': filing_status_gaps,
            'max_gap_pct': max_filing_status_gap,
            'within_1pct': filing_status_within_1pct,
            'within_5pct': filing_status_within_5pct,
            'total_categories': len(DOTAX_FILING_STATUS)
        },
        'agi_brackets': {
            'current': agi_bracket_sums.to_dict(),
            'target': DOTAX_AGI_BRACKETS,
            'gaps': agi_bracket_gaps,
            'max_gap_pct': max_agi_bracket_gap,
            'within_1pct': agi_bracket_within_1pct,
            'within_5pct': agi_bracket_within_5pct,
            'total_categories': len(DOTAX_AGI_BRACKETS)
        },
        'summary': {
            'filing_status_accuracy': filing_status_within_1pct / len(DOTAX_FILING_STATUS) * 100,
            'agi_bracket_accuracy': agi_bracket_within_1pct / len(DOTAX_AGI_BRACKETS) * 100,
            'overall_accuracy': (filing_status_within_1pct + agi_bracket_within_1pct) / 
                              (len(DOTAX_FILING_STATUS) + len(DOTAX_AGI_BRACKETS)) * 100
        }
    }
