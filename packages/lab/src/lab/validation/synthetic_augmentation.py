#!/usr/bin/env python3
"""
Synthetic Household Augmentation for Calibration

Generates synthetic tax units in under-sampled brackets to improve
calibration accuracy, especially for high-income brackets.

Strategy:
1. Identify brackets with small samples (< threshold)
2. Generate synthetic records by resampling existing records
3. Add controlled noise to avoid exact duplicates
4. Use lower initial weights for synthetic records
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)


def augment_undersampled_brackets(
    tax_units: pd.DataFrame,
    agi_col: str = 'agi',
    filing_status_col: str = 'filing_status',
    weight_col: str = 'weight',
    min_sample_size: int = 100,
    target_sample_size: int = 200,
    noise_factor: float = 0.05,
    synthetic_weight_factor: float = 0.1
) -> pd.DataFrame:
    """
    Augment under-sampled brackets with synthetic records.
    
    Args:
        tax_units: Original tax units DataFrame
        agi_col: Column name for AGI
        filing_status_col: Column name for filing status
        weight_col: Column name for weights
        min_sample_size: Minimum sample size to trigger augmentation
        target_sample_size: Target sample size after augmentation
        noise_factor: Amount of noise to add (as fraction of value)
        synthetic_weight_factor: Weight multiplier for synthetic records
        
    Returns:
        DataFrame with augmented records
    """
    df = tax_units.copy()
    df['is_synthetic'] = False
    
    # Define AGI brackets for analysis
    brackets = [
        (0, 50000),
        (50000, 100000),
        (100000, 150000),
        (150000, 200000),
        (200000, 300000),
        (300000, 400000),
        (400000, 999999999)
    ]
    
    synthetic_records = []
    augmentation_summary = []
    
    for status in df[filing_status_col].unique():
        for agi_min, agi_max in brackets:
            # Find records in this bracket
            mask = (df[filing_status_col] == status) & \
                   (df[agi_col] >= agi_min) & \
                   (df[agi_col] < agi_max) & \
                   (~df['is_synthetic'])  # Only use original records for sampling
            
            sample_size = mask.sum()
            
            # Skip if already have enough samples
            if sample_size >= min_sample_size or sample_size == 0:
                continue
            
            # Calculate how many synthetic records to generate
            n_synthetic = min(
                target_sample_size - sample_size,
                sample_size * 3  # Don't create more than 3x original
            )
            
            if n_synthetic <= 0:
                continue
            
            logger.info(f"Augmenting {status} ${agi_min//1000}k-${agi_max//1000}k: "
                       f"{sample_size} → {sample_size + n_synthetic} records")
            
            # Get source records
            source_records = df[mask].copy()
            
            # Resample with replacement
            synthetic = source_records.sample(n=n_synthetic, replace=True, random_state=42)
            
            # Add controlled noise to AGI and other numeric columns
            numeric_cols = synthetic.select_dtypes(include=[np.number]).columns
            for col in numeric_cols:
                if col in [agi_col, 'total_income']:
                    # Add noise to income/AGI (±5% by default)
                    noise = np.random.normal(1.0, noise_factor, size=len(synthetic))
                    synthetic[col] = synthetic[col] * noise
                    
                    # Ensure AGI stays within bracket
                    synthetic[col] = np.clip(synthetic[col], agi_min, agi_max - 1)
            
            # Reduce weight for synthetic records
            synthetic[weight_col] = synthetic[weight_col] * synthetic_weight_factor
            
            # Mark as synthetic
            synthetic['is_synthetic'] = True
            
            # Reset index to avoid duplicates
            synthetic = synthetic.reset_index(drop=True)
            
            synthetic_records.append(synthetic)
            
            augmentation_summary.append({
                'filing_status': status,
                'agi_min': agi_min,
                'agi_max': agi_max,
                'original_size': sample_size,
                'synthetic_added': n_synthetic,
                'final_size': sample_size + n_synthetic
            })
    
    if synthetic_records:
        # Combine original and synthetic
        augmented_df = pd.concat([df] + synthetic_records, ignore_index=True)
        
        # Log summary
        logger.info(f"\nSynthetic Augmentation Summary:")
        logger.info(f"  Original records: {len(df):,}")
        logger.info(f"  Synthetic records: {sum(len(s) for s in synthetic_records):,}")
        logger.info(f"  Total records: {len(augmented_df):,}")
        logger.info(f"  Brackets augmented: {len(augmentation_summary)}")
        
        return augmented_df
    else:
        logger.info("No augmentation needed - all brackets have sufficient samples")
        return df


def validate_synthetic_distribution(
    original_df: pd.DataFrame,
    augmented_df: pd.DataFrame,
    agi_col: str = 'agi',
    filing_status_col: str = 'filing_status'
):
    """
    Validate that synthetic augmentation preserves distributions.
    
    Args:
        original_df: Original tax units
        augmented_df: Augmented tax units
        agi_col: AGI column name
        filing_status_col: Filing status column name
    """
    logger.info("\nValidation: Distribution Comparison")
    logger.info("="*60)
    
    for status in original_df[filing_status_col].unique():
        orig_status = original_df[original_df[filing_status_col] == status]
        aug_status = augmented_df[
            (augmented_df[filing_status_col] == status) & 
            (~augmented_df['is_synthetic'])
        ]
        
        logger.info(f"\n{status}:")
        logger.info(f"  Original median AGI: ${orig_status[agi_col].median():,.0f}")
        logger.info(f"  Augmented median AGI: ${aug_status[agi_col].median():,.0f}")
        logger.info(f"  Original mean AGI: ${orig_status[agi_col].mean():,.0f}")
        logger.info(f"  Augmented mean AGI: ${aug_status[agi_col].mean():,.0f}")
