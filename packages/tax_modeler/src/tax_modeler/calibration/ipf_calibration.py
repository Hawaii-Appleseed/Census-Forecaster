"""
Iterative Proportional Fitting (IPF) for PUMS Weight Calibration

This module implements IPF (also known as raking) to adjust PUMS weights to match
2022 DOTAX and IRS SOI benchmarks across multiple dimensions:
- Total tax units
- Filing status distribution
- Income bracket distribution
- Geographic distribution (optional)

IPF adjusts weights iteratively to satisfy multiple marginal constraints simultaneously,
allowing 5-year PUMS data (2018-2022) to represent 2022 specifically.

Key Features:
- Multi-dimensional calibration (filing status × income brackets)
- Convergence monitoring with configurable tolerance
- Validation against DOTAX/IRS benchmarks
- Preserves PUMS microdata structure (only weights change)
"""

from __future__ import annotations

import logging
import warnings

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class IPFCalibrator:
    """
    Implements Iterative Proportional Fitting for PUMS weight calibration.
    
    The algorithm iteratively adjusts weights to match multiple marginal totals:
    1. Adjust weights to match filing status distribution
    2. Adjust weights to match income bracket distribution
    3. Repeat until convergence or max iterations reached
    """
    
    def __init__(self, 
                 benchmarks: dict,
                 max_iterations: int = 100,
                 tolerance: float = 0.001,
                 damping_factor: float = 0.7):
        """
        Initialize IPF calibrator.
        
        Args:
            benchmarks: Dictionary containing target distributions:
                - total_returns: Total number of tax returns
                - filing_status_distribution: Dict of {status: count}
                - income_bracket_distribution: Dict of {bracket: count}
            max_iterations: Maximum number of IPF iterations
            tolerance: Convergence tolerance (max relative difference)
            damping_factor: Damping factor for stability (0-1, lower = more stable)
        """
        self.benchmarks = benchmarks
        self.max_iterations = max_iterations
        self.tolerance = tolerance
        self.damping_factor = damping_factor
        
        # Validate benchmarks
        self._validate_benchmarks()
        
        # Track convergence history
        self.convergence_history = []
        
    def _validate_benchmarks(self):
        """Validate that required benchmarks are present."""
        required = ['total_returns']
        missing = [k for k in required if k not in self.benchmarks]
        
        if missing:
            raise ValueError(f"Missing required benchmarks: {missing}")
        
        # Check for at least one constraint dimension
        has_filing_status = 'filing_status_distribution' in self.benchmarks
        has_income_brackets = 'income_bracket_distribution' in self.benchmarks
        
        if not (has_filing_status or has_income_brackets):
            warnings.warn(
                "No filing status or income bracket constraints provided. "
                "IPF will only adjust to total returns."
            )
    
    def calibrate(self, 
                  tax_units: pd.DataFrame,
                  weight_col: str = 'weight',
                  filing_status_col: str = 'filing_status',
                  income_col: str = 'income') -> pd.DataFrame:
        """
        Calibrate PUMS weights using IPF.
        
        Args:
            tax_units: DataFrame with tax units
            weight_col: Name of weight column
            filing_status_col: Name of filing status column
            income_col: Name of income column
            
        Returns:
            DataFrame with calibrated weights in 'weight_calibrated' column
        """
        logger.info("Starting IPF calibration...")
        
        # Create working copy
        df = tax_units.copy()
        
        # Ensure weight column exists
        if weight_col not in df.columns:
            raise ValueError(f"Weight column '{weight_col}' not found")
        
        # Store original weights
        df['weight_original'] = df[weight_col]
        df['weight_calibrated'] = df[weight_col].copy()
        
        # Prepare constraint dimensions
        constraints = self._prepare_constraints(df, filing_status_col, income_col)
        
        # Run IPF iterations
        converged = False
        for iteration in range(self.max_iterations):
            # Store weights before iteration
            weights_before = df['weight_calibrated'].copy()
            
            # Apply each constraint dimension
            for constraint_name, constraint_data in constraints.items():
                df = self._apply_constraint(
                    df, 
                    constraint_name, 
                    constraint_data,
                    iteration
                )
            
            # Check convergence
            max_rel_change = self._calculate_convergence(weights_before, df['weight_calibrated'])
            self.convergence_history.append({
                'iteration': iteration + 1,
                'max_rel_change': max_rel_change,
                'total_weight': df['weight_calibrated'].sum()
            })
            
            logger.info(
                f"Iteration {iteration + 1}/{self.max_iterations}: "
                f"max_rel_change = {max_rel_change:.6f}, "
                f"total_weight = {df['weight_calibrated'].sum():,.0f}"
            )
            
            if max_rel_change < self.tolerance:
                logger.info(f"Converged after {iteration + 1} iterations")
                converged = True
                break
        
        if not converged:
            logger.warning(
                f"Did not converge after {self.max_iterations} iterations. "
                f"Final max_rel_change = {max_rel_change:.6f}"
            )
        
        # Add calibration metadata
        df['calibration_method'] = 'ipf'
        df['calibration_iterations'] = iteration + 1
        df['calibration_converged'] = converged
        
        # Validate results
        validation = self.validate_calibration(df)
        self._log_validation(validation)
        
        return df
    
    def _prepare_constraints(self, 
                            df: pd.DataFrame,
                            filing_status_col: str,
                            income_col: str) -> dict:
        """
        Prepare constraint dimensions for IPF.
        
        Returns:
            Dictionary of {constraint_name: constraint_data}
        """
        constraints = {}
        
        # Filing status constraint
        if 'filing_status_distribution' in self.benchmarks:
            # Normalize filing status values
            df['filing_status_normalized'] = (
                df[filing_status_col]
                .str.lower()
                .str.replace(' ', '_')
                .str.replace('married_filing_jointly', 'joint')
                .str.replace('married_filing_separately', 'mfs')
                .str.replace('head_of_household', 'hoh')
            )
            
            constraints['filing_status'] = {
                'column': 'filing_status_normalized',
                'targets': self._normalize_filing_status_targets()
            }
        
        # Income bracket constraint
        if 'income_bracket_distribution' in self.benchmarks:
            # Create income bracket column
            df['income_bracket'] = df[income_col].apply(self._assign_income_bracket)
            
            constraints['income_bracket'] = {
                'column': 'income_bracket',
                'targets': self.benchmarks['income_bracket_distribution']
            }
        
        return constraints
    
    def _normalize_filing_status_targets(self) -> dict[str, float]:
        """Normalize filing status target names to match PUMS conventions."""
        raw_targets = self.benchmarks['filing_status_distribution']
        normalized = {}
        
        for status, count in raw_targets.items():
            status_norm = (
                status.lower()
                .replace(' ', '_')
                .replace('married_filing_jointly', 'joint')
                .replace('married_filing_separately', 'mfs')
                .replace('head_of_household', 'hoh')
            )
            normalized[status_norm] = count
        
        return normalized
    
    def _assign_income_bracket(self, income: float) -> str:
        """Assign income to bracket (aligned with DOTAX SOI brackets)."""
        if pd.isna(income):
            return 'unknown'
        
        brackets = [
            (0, 10000, '$0-$10k'),
            (10000, 20000, '$10k-$20k'),
            (20000, 30000, '$20k-$30k'),
            (30000, 40000, '$30k-$40k'),
            (40000, 50000, '$40k-$50k'),
            (50000, 75000, '$50k-$75k'),
            (75000, 100000, '$75k-$100k'),
            (100000, 200000, '$100k-$200k'),
            (200000, 500000, '$200k-$500k'),
            (500000, 1000000, '$500k-$1M'),
            (1000000, float('inf'), '$1M+')
        ]
        
        for low, high, label in brackets:
            if low <= income < high:
                return label
        
        return 'unknown'
    
    def _apply_constraint(self,
                         df: pd.DataFrame,
                         constraint_name: str,
                         constraint_data: dict,
                         iteration: int) -> pd.DataFrame:
        """
        Apply a single constraint dimension using IPF.
        
        Args:
            df: DataFrame with current weights
            constraint_name: Name of constraint (for logging)
            constraint_data: Dict with 'column' and 'targets'
            iteration: Current iteration number
            
        Returns:
            DataFrame with adjusted weights
        """
        column = constraint_data['column']
        targets = constraint_data['targets']
        
        # Calculate current totals by category
        current_totals = df.groupby(column)['weight_calibrated'].sum()
        
        # Calculate adjustment factors for each category
        for category, target_count in targets.items():
            if category not in current_totals.index:
                logger.warning(
                    f"Category '{category}' in {constraint_name} not found in data. Skipping."
                )
                continue
            
            current_count = current_totals[category]
            
            if current_count == 0:
                logger.warning(
                    f"Category '{category}' has zero weight. Cannot adjust."
                )
                continue
            
            # Calculate raw adjustment factor
            raw_factor = target_count / current_count
            
            # Apply damping for stability (especially in early iterations)
            if iteration < 10:
                factor = 1 + (raw_factor - 1) * self.damping_factor
            else:
                # Less damping in later iterations for faster convergence
                factor = 1 + (raw_factor - 1) * min(0.9, self.damping_factor * 1.3)
            
            # Apply adjustment to all records in this category
            mask = df[column] == category
            df.loc[mask, 'weight_calibrated'] *= factor
        
        return df
    
    def _calculate_convergence(self, 
                               weights_before: pd.Series,
                               weights_after: pd.Series) -> float:
        """
        Calculate maximum relative change in weights.
        
        Returns:
            Maximum relative change across all weights
        """
        # Avoid division by zero
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            rel_changes = np.abs((weights_after - weights_before) / weights_before)
        
        # Replace inf/nan with 0
        rel_changes = rel_changes.replace([np.inf, -np.inf], 0).fillna(0)
        
        return rel_changes.max()
    
    def validate_calibration(self, df: pd.DataFrame) -> dict:
        """
        Validate calibrated weights against benchmarks.
        
        Returns:
            Dictionary with validation metrics
        """
        validation = {
            'total_weight': df['weight_calibrated'].sum(),
            'target_total': self.benchmarks['total_returns'],
            'dimensions': {}
        }
        
        # Overall total validation
        total_diff = validation['total_weight'] - validation['target_total']
        total_pct_diff = (total_diff / validation['target_total']) * 100
        validation['total_difference'] = total_diff
        validation['total_pct_difference'] = total_pct_diff
        
        # Filing status validation
        if 'filing_status_distribution' in self.benchmarks:
            if 'filing_status_normalized' in df.columns:
                current = df.groupby('filing_status_normalized')['weight_calibrated'].sum()
                targets = self._normalize_filing_status_targets()
                
                filing_status_validation = {}
                for status, target in targets.items():
                    actual = current.get(status, 0)
                    diff = actual - target
                    pct_diff = (diff / target * 100) if target > 0 else 0
                    
                    filing_status_validation[status] = {
                        'actual': actual,
                        'target': target,
                        'difference': diff,
                        'pct_difference': pct_diff
                    }
                
                validation['dimensions']['filing_status'] = filing_status_validation
        
        # Income bracket validation
        if 'income_bracket_distribution' in self.benchmarks:
            if 'income_bracket' in df.columns:
                current = df.groupby('income_bracket')['weight_calibrated'].sum()
                targets = self.benchmarks['income_bracket_distribution']
                
                income_validation = {}
                for bracket, target in targets.items():
                    actual = current.get(bracket, 0)
                    diff = actual - target
                    pct_diff = (diff / target * 100) if target > 0 else 0
                    
                    income_validation[bracket] = {
                        'actual': actual,
                        'target': target,
                        'difference': diff,
                        'pct_difference': pct_diff
                    }
                
                validation['dimensions']['income_bracket'] = income_validation
        
        return validation
    
    def _log_validation(self, validation: dict):
        """Log validation results."""
        logger.info("\n" + "="*80)
        logger.info("IPF CALIBRATION VALIDATION RESULTS")
        logger.info("="*80)
        
        # Overall total
        logger.info("\nTotal Tax Units:")
        logger.info(f"  Calibrated: {validation['total_weight']:,.0f}")
        logger.info(f"  Target:     {validation['target_total']:,.0f}")
        logger.info(f"  Difference: {validation['total_difference']:+,.0f} ({validation['total_pct_difference']:+.2f}%)")
        
        # Filing status
        if 'filing_status' in validation['dimensions']:
            logger.info("\nFiling Status Distribution:")
            logger.info(f"  {'Status':<30} {'Actual':>12} {'Target':>12} {'Diff %':>10}")
            logger.info(f"  {'-'*30} {'-'*12} {'-'*12} {'-'*10}")
            
            for status, metrics in validation['dimensions']['filing_status'].items():
                logger.info(
                    f"  {status:<30} {metrics['actual']:>12,.0f} "
                    f"{metrics['target']:>12,.0f} {metrics['pct_difference']:>+9.2f}%"
                )
        
        # Income brackets
        if 'income_bracket' in validation['dimensions']:
            logger.info("\nIncome Bracket Distribution:")
            logger.info(f"  {'Bracket':<20} {'Actual':>12} {'Target':>12} {'Diff %':>10}")
            logger.info(f"  {'-'*20} {'-'*12} {'-'*12} {'-'*10}")
            
            for bracket, metrics in validation['dimensions']['income_bracket'].items():
                logger.info(
                    f"  {bracket:<20} {metrics['actual']:>12,.0f} "
                    f"{metrics['target']:>12,.0f} {metrics['pct_difference']:>+9.2f}%"
                )
        
        logger.info("="*80)


def create_benchmarks_from_dotax(dotax_parser=None) -> dict:
    """
    Create IPF benchmarks from DOTAX SOI 2022 data.
    
    Args:
        dotax_parser: Optional DOTAXSOIParser instance
        
    Returns:
        Dictionary with benchmark targets for IPF
    """
    from .dotax_soi_parser import DOTAXSOIParser
    
    if dotax_parser is None:
        dotax_parser = DOTAXSOIParser()
    
    # Get resident totals (DOTAX primary benchmark)
    resident_totals = dotax_parser.get_resident_totals()
    
    # Get filing status distribution
    filing_status_df = dotax_parser.parse_filing_status_summary()
    filing_status_df = filing_status_df[~filing_status_df['filing_status'].str.contains('TOTAL', na=False)]
    
    filing_status_dist = {}
    for _, row in filing_status_df.iterrows():
        status = row['filing_status'].strip().lower()
        # Normalize status names
        if 'single' in status and 'married' not in status:
            status = 'single'
        elif 'married filing jointly' in status or 'joint' in status:
            status = 'joint'
        elif 'head of household' in status:
            status = 'hoh'
        elif 'married filing separately' in status:
            status = 'mfs'
        else:
            continue
        
        filing_status_dist[status] = int(row['num_returns'])
    
    logger.info(f"Filing status distribution loaded: {filing_status_dist}")
    
    # Get income bracket distributions from tax rate tables
    # Combine all filing statuses for overall income distribution
    income_brackets = {}
    for filing_status in ['single_mfs', 'mfj_qw', 'hoh']:
        rates_df = dotax_parser.parse_tax_rates_by_income(filing_status)
        
        for _, row in rates_df.iterrows():
            min_val = row['income_bracket_min']
            max_val = row['income_bracket_max']
            
            # Create bracket label
            if min_val == 0:
                bracket = '$0-$10k'
            elif max_val == np.inf:
                if min_val >= 1000000:
                    bracket = '$1M+'
                elif min_val >= 500000:
                    bracket = '$500k-$1M'
                elif min_val >= 200000:
                    bracket = '$200k-$500k'
                else:
                    bracket = f'${min_val//1000}k+'
            else:
                bracket = f'${min_val//1000}k-${max_val//1000}k'
            
            # Aggregate across filing statuses
            if bracket not in income_brackets:
                income_brackets[bracket] = 0
            income_brackets[bracket] += int(row['num_returns'])
    
    benchmarks = {
        'total_returns': resident_totals['total_returns'],
        'filing_status_distribution': filing_status_dist,
        'income_bracket_distribution': income_brackets,
        'total_agi': resident_totals['total_agi_positive'],
        'total_tax_before_credits': resident_totals['total_tax_before'],
        'total_tax_after_credits': resident_totals['total_tax_after']
    }
    
    logger.info("Created IPF benchmarks from DOTAX SOI 2022:")
    logger.info(f"  Total returns: {benchmarks['total_returns']:,}")
    logger.info(f"  Filing statuses: {len(benchmarks['filing_status_distribution'])}")
    logger.info(f"  Income brackets: {len(benchmarks['income_bracket_distribution'])}")
    
    return benchmarks


def calibrate_pums_with_ipf(tax_units: pd.DataFrame,
                            benchmarks: dict | None = None,
                            max_iterations: int = 100,
                            tolerance: float = 0.001) -> pd.DataFrame:
    """
    Convenience function to calibrate PUMS tax units using IPF.
    
    Args:
        tax_units: DataFrame with PUMS tax units
        benchmarks: Optional benchmark dictionary (will load from DOTAX if not provided)
        max_iterations: Maximum IPF iterations
        tolerance: Convergence tolerance
        
    Returns:
        DataFrame with calibrated weights
    """
    # Load benchmarks if not provided
    if benchmarks is None:
        logger.info("Loading DOTAX benchmarks...")
        benchmarks = create_benchmarks_from_dotax()
    
    # Initialize calibrator
    calibrator = IPFCalibrator(
        benchmarks=benchmarks,
        max_iterations=max_iterations,
        tolerance=tolerance
    )
    
    # Run calibration
    calibrated = calibrator.calibrate(tax_units)
    
    return calibrated
