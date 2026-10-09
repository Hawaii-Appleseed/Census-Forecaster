"""
Systematic Tax Calibration Orchestrator

Implements industrial-grade calibration framework following best practices from
TPC, CBO, Treasury, and ITEP microsimulation models.

Calibration Order (Most to Least Structural):
1. Weight calibration - Match population/filer totals
2. Income/AGI distribution - Match bracket shares
3. Synthetic tail calibration - Add ultra-high-income filers
4. Itemization/deduction calibration - Match deduction rates
5. Tax liability gap-closer - Match observed tax by bracket

Key Principle: Iterative feedback loop with validation after each step
to ensure no calibration undermines another.
"""

from __future__ import annotations

import pandas as pd
import numpy as np
import logging
from typing import Dict, Tuple, List, Callable

from tax_modeler.calibration import dotax_base

logger = logging.getLogger(__name__)


class CalibrationOrchestrator:
    """
    Orchestrates systematic, feedback-driven calibration pipeline.
    
    Each calibration step:
    1. Performs its adjustment
    2. Validates all prior constraints still hold
    3. Logs deviations for transparency
    
    The full pipeline iterates until all constraints are satisfied
    within tolerance, or max iterations reached.
    """
    
    # DOTAX targets: Table A-8 (tax, $M; filers) and Table 4 (filing status),
    # newest edition on file. One copy in ``dotax_base``.
    DOTAX_TAX_TARGETS = dotax_base.tax_targets_M()
    DOTAX_FILER_TARGETS = dotax_base.filer_targets()
    DOTAX_FILING_STATUS_TARGETS = dotax_base.status_targets()

    def __init__(self, max_iterations: int = 5, tolerance: float = 0.05):
        """
        Initialize calibration orchestrator.
        
        Args:
            max_iterations: Maximum calibration iterations
            tolerance: Tolerance for constraint validation (5% default)
        """
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.constraints = {}
        self.validation_history = []
        
    def validate_filer_counts(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Validate filer counts against DOTax targets.
        
        Returns:
            Dict mapping bracket to deviation percentage
        """
        deviations = {}
        
        for (min_agi, max_agi), target in self.DOTAX_FILER_TARGETS.items():
            mask = (df['agi'] >= min_agi) & (df['agi'] < max_agi)
            actual = df.loc[mask, 'weight'].sum()
            deviation = (actual - target) / target if target > 0 else 0
            deviations[f"${min_agi//1000}k-${max_agi//1000}k" if max_agi != float('inf') else f"${min_agi//1000}k+"] = deviation
            
        return deviations
    
    def validate_tax_totals(self, df: pd.DataFrame) -> Dict[str, float]:
        """
        Validate tax totals against DOTax targets.
        
        Returns:
            Dict mapping bracket to deviation percentage
        """
        deviations = {}
        
        for (min_agi, max_agi), target_m in self.DOTAX_TAX_TARGETS.items():
            mask = (df['agi'] >= min_agi) & (df['agi'] < max_agi)
            actual_m = (df.loc[mask, 'hi_state_tax'] * df.loc[mask, 'weight']).sum() / 1_000_000
            deviation = (actual_m - target_m) / target_m if target_m > 0 else 0
            deviations[f"${min_agi//1000}k-${max_agi//1000}k" if max_agi != float('inf') else f"${min_agi//1000}k+"] = deviation
            
        return deviations
    
    def validate_synthetic_preservation(self, df: pd.DataFrame) -> Dict[str, any]:
        """
        Validate synthetic filers are preserved with valid tax values.
        
        Returns:
            Dict with synthetic filer statistics
        """
        if 'is_synthetic_ultra_high' not in df.columns:
            return {'synthetic_count': 0, 'nan_tax_count': 0}
        
        synthetic_mask = df['is_synthetic_ultra_high'] == True
        nan_tax = df.loc[synthetic_mask, 'hi_state_tax'].isna().sum()
        
        return {
            'synthetic_count': synthetic_mask.sum(),
            'nan_tax_count': nan_tax,
            'valid': nan_tax == 0
        }
    
    def check_all_constraints(self, df: pd.DataFrame, step_name: str) -> bool:
        """
        Check all calibration constraints and log results.
        
        Args:
            df: DataFrame to validate
            step_name: Name of calibration step just completed
            
        Returns:
            True if all constraints within tolerance
        """
        logger.info(f"\n{'='*80}")
        logger.info(f"VALIDATION AFTER: {step_name}")
        logger.info(f"{'='*80}")
        
        all_valid = True
        
        # 1. Check filer counts
        filer_devs = self.validate_filer_counts(df)
        major_filer_violations = sum(1 for d in filer_devs.values() if abs(d) > self.tolerance)
        
        logger.info(f"\n1. Filer Count Validation:")
        logger.info(f"   Brackets within {self.tolerance*100:.0f}% tolerance: {len(filer_devs) - major_filer_violations}/{len(filer_devs)}")
        if major_filer_violations > 0:
            logger.warning(f"   ⚠️  {major_filer_violations} brackets exceed tolerance")
            all_valid = False
        else:
            logger.info(f"   ✅ All filer counts within tolerance")
        
        # 2. Check tax totals
        tax_devs = self.validate_tax_totals(df)
        major_tax_violations = sum(1 for d in tax_devs.values() if abs(d) > self.tolerance)
        
        logger.info(f"\n2. Tax Total Validation:")
        logger.info(f"   Brackets within {self.tolerance*100:.0f}% tolerance: {len(tax_devs) - major_tax_violations}/{len(tax_devs)}")
        if major_tax_violations > 0:
            logger.warning(f"   ⚠️  {major_tax_violations} brackets exceed tolerance")
            all_valid = False
        else:
            logger.info(f"   ✅ All tax totals within tolerance")
        
        # 3. Check synthetic preservation
        synthetic_stats = self.validate_synthetic_preservation(df)
        logger.info(f"\n3. Synthetic Filer Validation:")
        logger.info(f"   Synthetic filers: {synthetic_stats['synthetic_count']}")
        logger.info(f"   With NaN tax: {synthetic_stats['nan_tax_count']}")
        if synthetic_stats['synthetic_count'] > 0 and not synthetic_stats['valid']:
            logger.warning(f"   ⚠️  Synthetic filers have NaN tax values")
            all_valid = False
        elif synthetic_stats['synthetic_count'] > 0:
            logger.info(f"   ✅ Synthetic filers preserved")
        
        # Store validation result
        self.validation_history.append({
            'step': step_name,
            'filer_violations': major_filer_violations,
            'tax_violations': major_tax_violations,
            'synthetic_valid': synthetic_stats.get('valid', True),
            'all_valid': all_valid
        })
        
        return all_valid
    
    def calibrate(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Execute full calibration pipeline with feedback loop.
        
        Args:
            df: DataFrame with initial tax units
            
        Returns:
            Fully calibrated DataFrame
        """
        logger.info("\n" + "="*80)
        logger.info("SYSTEMATIC TAX CALIBRATION PIPELINE")
        logger.info("Following TPC/CBO/Treasury Best Practices")
        logger.info("="*80)
        
        result = df.copy()
        
        for iteration in range(self.max_iterations):
            logger.info(f"\n{'#'*80}")
            logger.info(f"CALIBRATION ITERATION {iteration + 1}/{self.max_iterations}")
            logger.info(f"{'#'*80}")
            
            iteration_start_valid = True
            
            # Step 1: Weight Calibration (Most Structural)
            result = self._step1_calibrate_weights(result)
            if not self.check_all_constraints(result, "Step 1: Weight Calibration"):
                iteration_start_valid = False
            
            # Step 2: Income/AGI Distribution Calibration
            result = self._step2_calibrate_income_distribution(result)
            if not self.check_all_constraints(result, "Step 2: Income Distribution"):
                iteration_start_valid = False
            
            # Step 3: Synthetic Tail Calibration
            result = self._step3_calibrate_synthetic_tail(result)
            if not self.check_all_constraints(result, "Step 3: Synthetic Tail"):
                iteration_start_valid = False
            
            # Step 4: Itemization/Deduction Calibration
            result = self._step4_calibrate_deductions(result)
            if not self.check_all_constraints(result, "Step 4: Deduction Calibration"):
                iteration_start_valid = False
            
            # Step 5: Tax Liability Gap-Closer
            result = self._step5_close_tax_gap(result)
            final_valid = self.check_all_constraints(result, "Step 5: Tax Gap Closer")
            
            # Check if all constraints satisfied
            if final_valid:
                logger.info(f"\n{'='*80}")
                logger.info(f"✅ ALL CONSTRAINTS SATISFIED - Calibration Complete")
                logger.info(f"{'='*80}")
                break
            elif iteration == self.max_iterations - 1:
                logger.warning(f"\n{'='*80}")
                logger.warning(f"⚠️  Max iterations reached - Some constraints not fully met")
                logger.warning(f"{'='*80}")
        
        return result
    
    def _step1_calibrate_weights(self, df: pd.DataFrame) -> pd.DataFrame:
        """Step 1: Calibrate weights to match filer counts."""
        logger.info("\n" + "="*80)
        logger.info("STEP 1: WEIGHT CALIBRATION")
        logger.info("="*80)
        
        from tax_modeler.adjustments.comprehensive_weight_calibrator import apply_comprehensive_weight_calibration
        
        result = df.copy()
        
        # Calibrate low/middle income brackets (<$200k)
        for (min_agi, max_agi), target_filers in self.DOTAX_FILER_TARGETS.items():
            if max_agi >= 200000:
                continue  # Skip high-income (handled by Pareto)
            
            mask = (result['agi'] >= min_agi) & (result['agi'] < max_agi)
            current_filers = result.loc[mask, 'weight'].sum()
            
            if current_filers > 0 and abs(current_filers - target_filers) > 100:
                adj_factor = target_filers / current_filers
                result.loc[mask, 'weight'] *= adj_factor
                logger.info(f"  ${min_agi//1000}k-${max_agi//1000}k: "
                           f"{current_filers:>8,.0f} → {target_filers:>8,} (×{adj_factor:.3f})")
        
        # Recalculate taxes after weight changes
        from tax_modeler.hawaii_calculator import HawaiiTaxCalculator
        calculator = HawaiiTaxCalculator()
        result = calculator.calculate_tax_units_batch(result)
        
        return result
    
    def _step2_calibrate_income_distribution(self, df: pd.DataFrame) -> pd.DataFrame:
        """Step 2: Calibrate income distribution to match effective rates."""
        logger.info("\n" + "="*80)
        logger.info("STEP 2: INCOME DISTRIBUTION CALIBRATION")
        logger.info("="*80)
        
        from tax_modeler.adjustments.income_distribution_calibrator import apply_income_distribution_calibration
        
        result = apply_income_distribution_calibration(
            df,
            threshold=100000,
            method='percentile',
            recalculate_tax=True
        )
        
        return result
    
    def _step3_calibrate_synthetic_tail(self, df: pd.DataFrame) -> pd.DataFrame:
        """Step 3: Add synthetic ultra-high-income filers."""
        logger.info("\n" + "="*80)
        logger.info("STEP 3: SYNTHETIC TAIL CALIBRATION")
        logger.info("="*80)
        
        from tax_modeler.adjustments.million_plus_reallocation import apply_million_plus_reallocation
        
        result = apply_million_plus_reallocation(
            df,
            reallocation_pct=0.35,
            target_tax_m=663.0,
            pareto_alpha=1.454
        )
        
        # DO NOT recalculate taxes here - synthetic filers already have tax values
        # This is critical to preserve synthetic filer tax calculations
        
        return result
    
    def _step4_calibrate_deductions(self, df: pd.DataFrame) -> pd.DataFrame:
        """Step 4: Calibrate itemized deductions."""
        logger.info("\n" + "="*80)
        logger.info("STEP 4: DEDUCTION CALIBRATION")
        logger.info("="*80)
        
        result = df.copy()
        
        # Reduce high-income deductions to increase tax
        mask_high_income = result['agi'] >= 200000
        
        if 'total_deductions' in result.columns:
            original_deductions = result.loc[mask_high_income, 'total_deductions'].sum()
            result.loc[mask_high_income, 'total_deductions'] *= 0.85
            new_deductions = result.loc[mask_high_income, 'total_deductions'].sum()
            
            logger.info(f"  $200k+ deductions: ${original_deductions/1e6:>8,.1f}M → ${new_deductions/1e6:>8,.1f}M (×0.85)")
        
        # Recalculate taxes after deduction changes (preserve synthetic filers)
        from tax_modeler.hawaii_calculator import HawaiiTaxCalculator
        calculator = HawaiiTaxCalculator()
        result = calculator.calculate_tax_units_batch(result)
        
        return result
    
    def _step5_close_tax_gap(self, df: pd.DataFrame) -> pd.DataFrame:
        """Step 5: Final gap closer with targeted adjustments."""
        logger.info("\n" + "="*80)
        logger.info("STEP 5: TAX GAP CLOSER")
        logger.info("="*80)
        
        from tax_modeler.adjustments.final_gap_closer import apply_final_gap_closer
        
        result = apply_final_gap_closer(
            df,
            recalculate_tax=False  # Don't recalculate - just apply multipliers
        )
        
        return result


def apply_systematic_calibration(df: pd.DataFrame,
                                  max_iterations: int = 5,
                                  tolerance: float = 0.05) -> pd.DataFrame:
    """
    Apply systematic calibration pipeline with feedback loop.
    
    Args:
        df: DataFrame with initial tax units
        max_iterations: Maximum calibration iterations
        tolerance: Tolerance for constraint validation (default 5%)
        
    Returns:
        Fully calibrated DataFrame
    """
    orchestrator = CalibrationOrchestrator(
        max_iterations=max_iterations,
        tolerance=tolerance
    )
    return orchestrator.calibrate(df)
