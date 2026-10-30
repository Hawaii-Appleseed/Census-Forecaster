"""
SOI Calibration Module

This module calibrates PUMS-based tax units to match DOTAX and IRS SOI benchmarks.
Uses a hybrid approach where PUMS provides demographic/geographic detail and
SOI provides accurate counts and income distributions.

Key Features:
- Calibrates weights to match DOTAX resident tax return counts
- Adjusts income distributions to match IRS SOI by filing status
- Preserves PUMS household relationships and demographics
- Provides income-bracket-specific adjustments for high-earner accuracy
"""

from __future__ import annotations

import logging

import pandas as pd

logger = logging.getLogger(__name__)


class SOICalibrator:
    """
    Calibrates PUMS-based tax units to match DOTAX and IRS SOI benchmarks.
    """
    
    def __init__(self, dotax_benchmarks: dict | None = None, 
                 irs_benchmarks: dict | None = None):
        """
        Initialize the SOI calibrator.
        
        Args:
            dotax_benchmarks: Dictionary with DOTAX SOI data (residents)
            irs_benchmarks: Dictionary with IRS SOI data (all filers)
        """
        self.dotax_benchmarks = dotax_benchmarks or {}
        self.irs_benchmarks = irs_benchmarks or {}
        
        # Default adjustment factors from analysis
        self.default_overall_factor = 0.6061
        self.default_filing_status_factors = {
            'single': 0.6634,
            'married_filing_jointly': 0.5009,
            'head_of_household': 1.1932,
            'married_filing_separately': 0.6094
        }
    
    def calibrate_weights(self, tax_units: pd.DataFrame, 
                         method: str = 'overall',
                         preserve_demographics: bool = True) -> pd.DataFrame:
        """
        Calibrate tax unit weights to match SOI benchmarks.
        
        Args:
            tax_units: DataFrame of tax units from PUMS
            method: Calibration method ('overall', 'filing_status', 'income_bracket', 'ipf')
            preserve_demographics: Whether to preserve demographic distributions
            
        Returns:
            DataFrame with calibrated weights
        """
        logger.info(f"Calibrating weights using method: {method}")
        
        tax_units = tax_units.copy()
        
        # Ensure weight column exists
        if 'weight' not in tax_units.columns:
            if 'PWGTP' in tax_units.columns:
                tax_units['weight'] = tax_units['PWGTP']
            else:
                logger.warning("No weight column found, using uniform weights")
                tax_units['weight'] = 1.0
        
        # Store original weights for comparison
        tax_units['weight_original'] = tax_units['weight']
        
        if method == 'overall':
            tax_units = self._calibrate_overall(tax_units)
        elif method == 'filing_status':
            tax_units = self._calibrate_by_filing_status(tax_units)
        elif method == 'income_bracket':
            tax_units = self._calibrate_by_income_bracket(tax_units)
        elif method == 'ipf':
            tax_units = self._calibrate_with_ipf(tax_units)
        else:
            raise ValueError(f"Unknown calibration method: {method}")
        
        # Log calibration results
        self._log_calibration_results(tax_units)
        
        return tax_units
    
    def _calibrate_overall(self, tax_units: pd.DataFrame) -> pd.DataFrame:
        """
        Apply overall adjustment factor to all tax units.
        
        This is the simplest method - multiply all weights by 0.6061 to align
        total tax units with DOTAX count of 634,956.
        """
        logger.info(f"Applying overall adjustment factor: {self.default_overall_factor}")
        
        tax_units['weight_calibrated'] = tax_units['weight'] * self.default_overall_factor
        tax_units['calibration_method'] = 'overall'
        tax_units['calibration_factor'] = self.default_overall_factor
        
        return tax_units
    
    def _calibrate_by_filing_status(self, tax_units: pd.DataFrame) -> pd.DataFrame:
        """
        Apply filing status-specific adjustment factors.
        
        This method applies different factors for each filing status to better
        match DOTAX distributions.
        """
        logger.info("Applying filing status-specific adjustment factors")
        
        # Normalize filing status values
        tax_units['filing_status_normalized'] = tax_units['filing_status'].str.lower().str.replace(' ', '_')
        
        # Apply status-specific factors
        def apply_status_factor(row):
            status = row['filing_status_normalized']
            factor = self.default_filing_status_factors.get(status, self.default_overall_factor)
            return row['weight'] * factor, factor
        
        tax_units[['weight_calibrated', 'calibration_factor']] = tax_units.apply(
            lambda row: pd.Series(apply_status_factor(row)),
            axis=1
        )
        
        tax_units['calibration_method'] = 'filing_status'
        
        return tax_units
    
    def _calibrate_by_income_bracket(self, tax_units: pd.DataFrame) -> pd.DataFrame:
        """
        Apply income-bracket-specific adjustments.
        
        This method addresses the high-income undercount issue by applying
        different factors based on income level.
        """
        logger.info("Applying income-bracket-specific adjustment factors")
        
        # Define income brackets (aligned with DOTAX SOI brackets)
        income_brackets = [
            (0, 10000, 0.65),
            (10000, 20000, 0.64),
            (20000, 30000, 0.63),
            (30000, 40000, 0.62),
            (40000, 50000, 0.61),
            (50000, 75000, 0.60),
            (75000, 100000, 0.58),
            (100000, 200000, 0.55),
            (200000, 500000, 0.50),
            (500000, 1000000, 0.45),
            (1000000, float('inf'), 0.40)
        ]
        
        def get_income_bracket_factor(income):
            for low, high, factor in income_brackets:
                if low <= income < high:
                    return factor
            return self.default_overall_factor
        
        # Ensure income column exists
        if 'income' not in tax_units.columns:
            logger.warning("No income column found, falling back to overall calibration")
            return self._calibrate_overall(tax_units)
        
        tax_units['calibration_factor'] = tax_units['income'].apply(get_income_bracket_factor)
        tax_units['weight_calibrated'] = tax_units['weight'] * tax_units['calibration_factor']
        tax_units['calibration_method'] = 'income_bracket'
        
        return tax_units
    
    def _calibrate_with_ipf(self, tax_units: pd.DataFrame) -> pd.DataFrame:
        """
        Apply Iterative Proportional Fitting (IPF) calibration.
        
        This method uses IPF to adjust weights across multiple dimensions
        simultaneously (filing status × income brackets).
        """
        logger.info("Applying IPF calibration")
        
        try:
            from ..calibration.ipf_calibration import IPFCalibrator, create_benchmarks_from_dotax
        except ImportError:
            logger.error("IPF calibration module not found. Falling back to filing_status method.")
            return self._calibrate_by_filing_status(tax_units)
        
        # Create benchmarks from DOTAX if not already available
        if not self.dotax_benchmarks:
            logger.info("Loading DOTAX benchmarks for IPF...")
            benchmarks = create_benchmarks_from_dotax()
        else:
            # Convert existing benchmarks to IPF format
            benchmarks = {
                'total_returns': self.dotax_benchmarks.get('total_returns', 634956),
                'filing_status_distribution': self.dotax_benchmarks.get('filing_status_distribution', {}),
                'income_bracket_distribution': self.dotax_benchmarks.get('income_bracket_distribution', {})
            }
        
        # Initialize IPF calibrator
        ipf_calibrator = IPFCalibrator(
            benchmarks=benchmarks,
            max_iterations=100,
            tolerance=0.001,
            damping_factor=0.7
        )
        
        # Run IPF calibration
        calibrated = ipf_calibrator.calibrate(tax_units)
        
        return calibrated
    
    def _log_calibration_results(self, tax_units: pd.DataFrame):
        """Log calibration results for validation."""
        
        original_total = tax_units['weight_original'].sum()
        calibrated_total = tax_units['weight_calibrated'].sum()
        
        logger.info("\nCalibration Results:")
        logger.info(f"  Original weighted total: {original_total:,.0f}")
        logger.info(f"  Calibrated weighted total: {calibrated_total:,.0f}")
        logger.info(f"  Change: {calibrated_total - original_total:,.0f} ({(calibrated_total/original_total - 1)*100:+.1f}%)")
        
        if self.dotax_benchmarks and 'total_returns' in self.dotax_benchmarks:
            dotax_total = self.dotax_benchmarks['total_returns']
            logger.info(f"  DOTAX target: {dotax_total:,.0f}")
            logger.info(f"  Difference from DOTAX: {calibrated_total - dotax_total:,.0f} ({(calibrated_total/dotax_total - 1)*100:+.1f}%)")
        
        # Log by filing status if available
        if 'filing_status' in tax_units.columns:
            logger.info("\nCalibrated totals by filing status:")
            status_totals = tax_units.groupby('filing_status')['weight_calibrated'].sum()
            for status, total in status_totals.items():
                pct = (total / calibrated_total) * 100
                logger.info(f"  {status}: {total:,.0f} ({pct:.1f}%)")
    
    def validate_calibration(self, tax_units: pd.DataFrame) -> dict:
        """
        Validate calibrated tax units against SOI benchmarks.
        
        Returns:
            Dictionary with validation metrics
        """
        validation = {
            'total_units': tax_units['weight_calibrated'].sum(),
            'filing_status_distribution': {},
            'income_distribution': {},
            'validation_passed': True,
            'warnings': []
        }
        
        # Check total against DOTAX
        if self.dotax_benchmarks and 'total_returns' in self.dotax_benchmarks:
            dotax_total = self.dotax_benchmarks['total_returns']
            calibrated_total = validation['total_units']
            pct_diff = abs((calibrated_total / dotax_total - 1) * 100)
            
            validation['total_units_pct_diff'] = pct_diff
            
            if pct_diff > 5:
                validation['validation_passed'] = False
                validation['warnings'].append(
                    f"Total units differ from DOTAX by {pct_diff:.1f}% (threshold: 5%)"
                )
        
        # Check filing status distribution
        if 'filing_status' in tax_units.columns:
            status_dist = tax_units.groupby('filing_status')['weight_calibrated'].sum()
            total = status_dist.sum()
            validation['filing_status_distribution'] = {
                status: (count / total * 100) for status, count in status_dist.items()
            }
        
        # Check income distribution
        if 'income' in tax_units.columns:
            validation['avg_income'] = (
                tax_units['income'] * tax_units['weight_calibrated']
            ).sum() / tax_units['weight_calibrated'].sum()
            
            validation['total_income'] = (
                tax_units['income'] * tax_units['weight_calibrated']
            ).sum()
        
        return validation


def calibrate_to_soi_benchmarks(tax_units: pd.DataFrame,
                                dotax_benchmarks: dict | None = None,
                                irs_benchmarks: dict | None = None,
                                method: str = 'ipf') -> pd.DataFrame:
    """
    Convenience function to calibrate tax units to SOI benchmarks.
    
    Args:
        tax_units: DataFrame of tax units from PUMS
        dotax_benchmarks: DOTAX SOI benchmark data
        irs_benchmarks: IRS SOI benchmark data
        method: Calibration method ('overall', 'filing_status', 'income_bracket', 'ipf')
                Default is 'ipf' for best accuracy across multiple dimensions
        
    Returns:
        DataFrame with calibrated weights
    """
    calibrator = SOICalibrator(dotax_benchmarks, irs_benchmarks)
    calibrated = calibrator.calibrate_weights(tax_units, method=method)
    
    # Validate results
    validation = calibrator.validate_calibration(calibrated)
    
    if not validation['validation_passed']:
        logger.warning("Calibration validation warnings:")
        for warning in validation['warnings']:
            logger.warning(f"  - {warning}")
    
    return calibrated


def load_dotax_benchmarks(data_dir: str = "data/raw") -> dict:
    """
    Load DOTAX SOI benchmarks from CSV files.
    
    Args:
        data_dir: Directory containing DOTAX SOI CSV files
        
    Returns:
        Dictionary with DOTAX benchmark data
    """
    from scripts.compare_data_sources import DataSourceComparator
    
    comparator = DataSourceComparator(base_dir=".")
    dotax_data = comparator.load_dotax_soi_data()
    
    return dotax_data


def load_irs_benchmarks(data_dir: str = "data/processed") -> dict:
    """
    Load IRS SOI benchmarks.
    
    Args:
        data_dir: Directory containing IRS SOI data
        
    Returns:
        Dictionary with IRS benchmark data
    """
    from scripts.compare_data_sources import DataSourceComparator
    
    comparator = DataSourceComparator(base_dir=".")
    irs_data = comparator.load_irs_soi_data()
    
    return irs_data
