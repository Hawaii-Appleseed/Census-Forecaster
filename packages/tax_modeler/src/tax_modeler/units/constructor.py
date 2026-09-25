"""
Tax Unit Constructor

This module provides the main TaxUnitConstructor class that uses the modular components
to construct tax units from PUMS data with performance optimizations including batch processing,
parallel execution, and memory efficiency.
"""

from __future__ import annotations

import logging
import time
import os
import multiprocessing as mp
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple, Union, Any, Generator, Set
import pandas as pd
import numpy as np
from tqdm import tqdm

# Import modular components
from .status import is_married_filing_jointly, is_married_filing_separately, is_head_of_household
from .income import calculate_tax_unit_income, extract_person_income_components
from .dependencies import identify_dependents
from .utils import setup_logging, validate_input_data, create_person_id
from .fixes import apply_overcounting_fixes

# Import base validation components
from .validation import (
    TaxUnitValidator, 
    ValidationIssue, 
    ValidationSeverity,
    MLTaxUnitValidator,
    ML_VALIDATOR_AVAILABLE
)

# Import IRS-based methods
try:
    from tax_modeler.units.status.irs_based import should_file_jointly_irs_method, calibrate_to_soi_totals
except ImportError:
    should_file_jointly_irs_method = None
    calibrate_to_soi_totals = None

# Constants for optimization
DEFAULT_BATCH_SIZE = 1000
DEFAULT_NUM_PROCESSES = max(1, mp.cpu_count() - 1)

# Configure logging
logger = logging.getLogger(__name__)


class TaxUnitConstructor:
    """
    Main class for constructing tax units from PUMS data using modular components.
    
    This class orchestrates the tax unit construction process by leveraging
    specialized modules for different aspects of the process.
    """
    
    # Maximum number of tax units allowed per household
    # Set to None (unlimited) to ensure all adults can create tax units
    # Analysis showed we need to capture all adults to reach 100% SOI coverage
    # Will rely on SOI calibration to adjust filing status distribution
    MAX_TAX_UNITS_PER_HOUSEHOLD = None  # Unlimited
    
    def __init__(self, person_df: pd.DataFrame, hh_df: pd.DataFrame,
                 batch_size: int = None, num_processes: int = None,
                 progress_bar: bool = True, ml_model_path: str = None,
                 use_soi_calibration: bool = True,
                 soi_calibration_method: str = 'filing_status',
                 dotax_benchmarks: Optional[Dict] = None,
                 irs_benchmarks: Optional[Dict] = None,
                 include_replicate_weights: bool = True):
        """
        Initialize the TaxUnitConstructor.

        Args:
            person_df: DataFrame containing person-level PUMS data
            hh_df: DataFrame containing household-level PUMS data
            batch_size: Number of households to process in each batch (for parallel processing)
            num_processes: Number of processes to use for parallel processing
            progress_bar: Whether to show a progress bar during processing
            ml_model_path: Path to trained ML model for validation (optional)
            use_soi_calibration: Whether to apply SOI calibration to weights (default: True)
            soi_calibration_method: Method for SOI calibration ('overall', 'filing_status', 'income_bracket')
            dotax_benchmarks: DOTAX SOI benchmark data (optional, will load if not provided)
            irs_benchmarks: IRS SOI benchmark data (optional, will load if not provided)
            include_replicate_weights: When True (default), propagate PUMS replicate
                weights (WGTP1..WGTP80 for households, PWGTP1..PWGTP80 for persons)
                through to the tax-unit output as weight_r01..weight_r80. Required
                for SDR variance estimation downstream (compute_poverty_impact_with_se,
                estimate_hi_eitc_takeup). Auto-no-ops if the replicate columns are
                missing from the input frames. Each replicate adds ~0.3 MB to
                a 42K-row tax-unit frame.
        """
        self.person_df = person_df.copy()
        self.hh_df = hh_df.copy()
        self.tax_units = None
        
        # Performance settings
        self.batch_size = batch_size or DEFAULT_BATCH_SIZE
        self.num_processes = num_processes or DEFAULT_NUM_PROCESSES
        self.progress_bar = progress_bar and (len(person_df) > 1000)  # Only show for large datasets
        
        # SOI calibration settings
        self.use_soi_calibration = use_soi_calibration
        self.soi_calibration_method = soi_calibration_method
        self.dotax_benchmarks = dotax_benchmarks
        self.irs_benchmarks = irs_benchmarks

        # Replicate-weight propagation (auto-no-op if PUMS replicates aren't loaded)
        self.include_replicate_weights = include_replicate_weights
        if include_replicate_weights:
            # Detect at construction time so per-row work in the hot path can
            # branch on a precomputed list instead of re-checking 80 columns.
            self._wgtp_replicate_cols = [
                f"WGTP{r}" for r in range(1, 81)
                if f"WGTP{r}" in self.hh_df.columns
            ]
            self._pwgtp_replicate_cols = [
                f"PWGTP{r}" for r in range(1, 81)
                if f"PWGTP{r}" in self.person_df.columns
            ]
        else:
            self._wgtp_replicate_cols = []
            self._pwgtp_replicate_cols = []
        
        # Initialize validator
        self.validator = TaxUnitValidator()
        
        # Initialize ML validator if model path is provided
        self.ml_validator = MLTaxUnitValidator(ml_model_path) if ml_model_path else None
        if self.ml_validator and self.ml_validator.model is None:
            logger.warning("Failed to load ML model, falling back to rule-based validation")
        
        # Setup logging
        setup_logging()
        logger.info(f"Initialized TaxUnitConstructor with batch_size={self.batch_size}, "
                   f"num_processes={self.num_processes}, "
                   f"max_tax_units_per_household={self.MAX_TAX_UNITS_PER_HOUSEHOLD}, "
                   f"use_soi_calibration={use_soi_calibration}")
        
        # Validate inputs
        is_valid, error_msg = validate_input_data(self.person_df, self.hh_df)
        if not is_valid:
            raise ValueError(f"Invalid input data: {error_msg}")
        
        # Preprocess data
        self._preprocess_data()
    
    def _preprocess_data(self) -> None:
        """Preprocess the input data for tax unit construction."""
        # Check if person_id already exists as index
        if self.person_df.index.name == 'person_id':
            # Person IDs already set up correctly, don't recreate them
            logger.debug("Person IDs already exist as index, preserving them")
        else:
            # Create person_id from SERIALNO and SPORDER
            self.person_df['person_id'] = create_person_id(self.person_df)
            
            # Set person_id as the index
            self.person_df.set_index('person_id', inplace=True)
        
        # Add is_adult flag
        self.person_df['is_adult'] = self.person_df['AGEP'] >= 18
        self.person_df['is_child'] = ~self.person_df['is_adult']
        
        # Merge household data - preserve the index
        original_index = self.person_df.index
        self.person_df = self.person_df.merge(
            self.hh_df[['SERIALNO', 'HINCP']], 
            on='SERIALNO', 
            how='left'
        )
        # Restore the original index
        self.person_df.index = original_index
    
    def _process_household_batch(self, batch: List[Tuple[str, pd.DataFrame]]) -> Tuple[List[dict], List[dict]]:
        """
        Process a batch of households.
        
        Args:
            batch: List of (household_id, household_data) tuples
            
        Returns:
            Tuple of (tax_units, validation_issues) for the batch
        """
        batch_units = []
        batch_issues = []
        
        for hh_id, hh_group in batch:
            try:
                logger.debug(f"Processing household {hh_id} in batch")
                household_units = self._process_household(hh_group)
                
                if household_units:
                    # Run standard validation
                    for unit in household_units:
                        issues = self.validator.validate_tax_unit(unit)
                        batch_issues.extend(issues)
                    
                    # Run ML-based validation if available
                    if self.ml_validator and household_units:
                        try:
                            # Run ML validation
                            validated_units = self.ml_validator.predict(household_units)
                            
                            # Update units with ML validation flags
                            for i, unit in enumerate(household_units):
                                if 'validation_flags' in validated_units[i]:
                                    unit['ml_validation_flags'] = validated_units[i]['validation_flags']
                                    
                                    # Log ML validation flags
                                    for flag in validated_units[i]['validation_flags']:
                                        batch_issues.append({
                                            'household_id': hh_id,
                                            'filer_id': unit.get('filer_id', 'unknown'),
                                            'severity': 'WARNING',
                                            'message': f"ML Validation: {flag['message']} (Confidence: {flag.get('confidence', 0.0):.2f})",
                                            'suggested_status': flag.get('suggested_status')
                                        })
                        except Exception as e:
                            logger.error(f"Error in ML validation for household {hh_id}: {str(e)}", exc_info=True)
                    
                    # Validate household coverage
                    coverage_issues = self.validator.validate_household_coverage(
                        household_units, hh_group
                    )
                    batch_issues.extend(coverage_issues)
                    
                    batch_units.extend(household_units)
                
            except Exception as e:
                error_msg = f"Error processing household {hh_id}: {str(e)}"
                logger.error(error_msg, exc_info=True)
                batch_issues.append({
                    'household_id': hh_id,
                    'error': error_msg,
                    'severity': 'ERROR'
                })
        
        return batch_units, batch_issues
    
    def _create_batches(self, data: pd.DataFrame) -> List[List[Tuple[str, pd.DataFrame]]]:
        """
        Split data into batches for parallel processing.
        
        Args:
            data: DataFrame containing person data with SERIALNO column
            
        Returns:
            List of batches, where each batch is a list of (household_id, household_data) tuples
        """
        # Group by household
        households = list(data.groupby('SERIALNO'))
        
        # Create batches
        batches = []
        for i in range(0, len(households), self.batch_size):
            batch = households[i:i + self.batch_size]
            batches.append(batch)
        
        logger.info(f"Created {len(batches)} batches with up to {self.batch_size} households each")
        return batches
    
    def create_rule_based_units(self, parallel: bool = True) -> pd.DataFrame:
        """
        Create tax units using rule-based approach with optional parallel processing.
        
        This method processes households in batches, with each batch optionally processed
        in parallel. It handles the following types of tax units:
        - Married filing jointly
        - Married filing separately
        - Head of household
        - Single filers
        
        Args:
            parallel: Whether to use parallel processing (default: True)
            
        Returns:
            DataFrame containing the constructed tax units with validation results
            
        Raises:
            ValueError: If input data is invalid or processing fails
        """
        start_time = time.time()
        logger.info(f"Creating tax units using rule-based approach (parallel={parallel}, "
                   f"processes={self.num_processes}, batch_size={self.batch_size})...")
        
        tax_units = []
        validation_issues = []
        
        # Create batches of households
        batches = self._create_batches(self.person_df)
        
        # Process batches
        if parallel and self.num_processes > 1:
            # Process batches in parallel
            with ProcessPoolExecutor(max_workers=self.num_processes) as executor:
                # Submit all batches
                future_to_batch = {
                    executor.submit(self._process_household_batch, batch): i 
                    for i, batch in enumerate(batches)
                }
                
                # Process results as they complete
                for future in (tqdm(as_completed(future_to_batch), 
                                 total=len(batches),
                                 desc="Processing households",
                                 disable=not self.progress_bar) 
                             if self.progress_bar else as_completed(future_to_batch)):
                    
                    batch_units, batch_issues = future.result()
                    tax_units.extend(batch_units)
                    validation_issues.extend(batch_issues)
        else:
            # Process batches sequentially
            for batch in (tqdm(batches, desc="Processing households", disable=not self.progress_bar) 
                         if self.progress_bar else batches):
                batch_units, batch_issues = self._process_household_batch(batch)
                tax_units.extend(batch_units)
                validation_issues.extend(batch_issues)
        
        # Log validation issues
        if validation_issues:
            self._log_validation_issues(validation_issues)
        
        # Calculate performance metrics
        end_time = time.time()
        processing_time = end_time - start_time
        units_per_second = len(tax_units) / processing_time if processing_time > 0 else 0
        
        # Log performance summary
        logger.info(f"Processed {len(tax_units):,} tax units from {len(self.person_df['SERIALNO'].unique()):,} "
                  f"households in {processing_time:.2f} seconds ({units_per_second:.1f} units/sec)")
        
        # Calculate error counts for validation summary
        def get_severity(issue):
            if hasattr(issue, 'severity'):
                return issue.severity
            elif isinstance(issue, dict):
                return issue.get('severity')
            else:
                return 'INFO'
        
        error_count = sum(1 for issue in validation_issues if get_severity(issue) == 'ERROR')
        warning_count = sum(1 for issue in validation_issues if get_severity(issue) == 'WARNING')
        
        # Convert to DataFrame with optimized dtypes
        if tax_units:
            # Create DataFrame with optimized dtypes
            self.tax_units = pd.DataFrame(tax_units)
            
            # Optimize column dtypes
            self._optimize_dataframe_dtypes()
            
            self.validation_summary = {
                'total_units': len(tax_units),
                'total_households': len(self.person_df['SERIALNO'].unique()),
                'processing_time_seconds': processing_time,
                'units_per_second': units_per_second,
                'validation_issues': len(validation_issues),
                'validation_errors': error_count,
                'validation_warnings': warning_count,
                'has_errors': error_count > 0,
                'batch_size': self.batch_size,
                'num_processes': self.num_processes if parallel and self.num_processes > 1 else 1
            }
            
            logger.info(f"Validation summary: {len(tax_units):,} tax units created, "
                      f"{error_count} errors, {warning_count} warnings")
            
            # Apply comprehensive overcounting fixes
            logger.info("Applying overcounting fixes...")
            fixed_person_df, fixed_hh_df, fixed_tax_units, fix_validation = apply_overcounting_fixes(
                self.person_df, self.hh_df, tax_units
            )
            
            # Update the tax_units DataFrame with fixed data
            if fixed_tax_units:
                # Apply SOI calibration if enabled
                if self.use_soi_calibration:
                    logger.info(f"Applying SOI calibration using method: {self.soi_calibration_method}")
                    
                    # Import SOI calibration module
                    from .soi_calibration import calibrate_to_soi_benchmarks
                    
                    # Convert to DataFrame for calibration
                    fixed_df = pd.DataFrame(fixed_tax_units)
                    
                    # Apply calibration
                    calibrated_df = calibrate_to_soi_benchmarks(
                        fixed_df,
                        dotax_benchmarks=self.dotax_benchmarks,
                        irs_benchmarks=self.irs_benchmarks,
                        method=self.soi_calibration_method
                    )
                    
                    # Use calibrated weights
                    if 'weight_calibrated' in calibrated_df.columns:
                        calibrated_df['weight'] = calibrated_df['weight_calibrated']
                    
                    self.tax_units = calibrated_df
                    calibrated_count = len(calibrated_df)
                    soi_calibration_applied = True
                else:
                    logger.info("SOI calibration disabled - using natural PUMS weights")
                    self.tax_units = pd.DataFrame(fixed_tax_units)
                    calibrated_count = 0
                    soi_calibration_applied = False
                
                self._optimize_dataframe_dtypes()
                
                # Update validation summary with fix and calibration results
                self.validation_summary.update({
                    'overcounting_fixes_applied': True,
                    'fixes_validation': fix_validation,
                    'soi_calibration_applied': soi_calibration_applied,
                    'soi_calibration_method': self.soi_calibration_method if soi_calibration_applied else None,
                    'calibrated_units': calibrated_count,
                    'final_tax_units': len(self.tax_units)
                })
                
                logger.info(f"Processing complete: {len(tax_units):,} -> {len(fixed_tax_units):,} -> {len(self.tax_units):,} tax units (after fixes and calibration)")
            else:
                logger.warning("No tax units after applying fixes")
        else:
            # Empty result with proper column dtypes
            self.tax_units = pd.DataFrame({
                'filer_id': pd.Series(dtype='string'),
                'filing_status': pd.Series(dtype='category'),
                'income': pd.Series(dtype='float64'),
                'num_dependents': pd.Series(dtype='int32'),
                'dependents': pd.Series(dtype='object'),  # List of strings
                'hh_id': pd.Series(dtype='string'),
                'primary_filer_id': pd.Series(dtype='string'),
                'secondary_filer_id': pd.Series(dtype='string')
            })
            
            self.validation_summary = {
                'total_units': 0,
                'total_households': len(self.person_df['SERIALNO'].unique()),
                'processing_time_seconds': processing_time,
                'units_per_second': 0,
                'validation_issues': len(validation_issues),
                'validation_errors': error_count,
                'validation_warnings': warning_count,
                'has_errors': error_count > 0,
                'batch_size': self.batch_size,
                'num_processes': self.num_processes if parallel and self.num_processes > 1 else 1,
                'overcounting_fixes_applied': False
            }
            
        return self.tax_units
    
    def _optimize_dataframe_dtypes(self) -> None:
        """
        Optimize the data types of the tax_units DataFrame to reduce memory usage.
        
        This method should be called after creating the tax_units DataFrame.
        """
        if self.tax_units is None or self.tax_units.empty:
            return
            
        # Convert string columns to categorical where appropriate
        for col in self.tax_units.columns:
            # Skip non-string columns
            if not pd.api.types.is_string_dtype(self.tax_units[col]):
                continue
                
            # Skip columns with missing values: a categorical with NaN values
            # grouped via groupby(dropna=False) raises "Categorical categories
            # cannot be null" on some pandas versions (e.g. the one resolved
            # under Python 3.10). Geography columns like `county` carry nulls
            # for group quarters / unmapped PUMAs and are grouped downstream,
            # so leave any null-containing column as object.
            if self.tax_units[col].isna().any():
                continue

            # For string columns with low cardinality, use categorical
            num_unique = len(self.tax_units[col].unique())
            num_rows = len(self.tax_units)

            if 1 < num_unique < (num_rows * 0.5):  # Less than 50% unique values
                self.tax_units[col] = self.tax_units[col].astype('category')
                
        # Downcast numeric columns
        for col in self.tax_units.select_dtypes(include=['int64', 'float64']).columns:
            if pd.api.types.is_integer_dtype(self.tax_units[col]):
                # Downcast integers
                self.tax_units[col] = pd.to_numeric(self.tax_units[col], downcast='integer')
            else:
                # Downcast floats
                self.tax_units[col] = pd.to_numeric(self.tax_units[col], downcast='float')
        
        # Log memory savings
        mem_before = self.tax_units.memory_usage(deep=True).sum() / 1024**2  # MB
        mem_after = self.tax_units.memory_usage(deep=True).sum() / 1024**2  # MB
        logger.info(f"Optimized DataFrame memory usage: {mem_before:.2f}MB -> {mem_after:.2f}MB "
                  f"({((mem_before - mem_after) / mem_before * 100):.1f}% reduction)")
    
    def _log_validation_issues(self, issues: List[Dict[str, Any]]) -> None:
        """
        Log validation issues at appropriate severity levels.
        
        Args:
            issues: List of validation issues
        """
        if not issues:
            return
            
        # Handle both dict and ValidationIssue objects
        def get_severity(issue):
            if hasattr(issue, 'severity'):
                return issue.severity
            elif isinstance(issue, dict):
                return issue.get('severity')
            else:
                return 'INFO'
        
        def get_message(issue):
            if hasattr(issue, 'message'):
                return issue.message
            elif isinstance(issue, dict):
                return issue.get('message', str(issue))
            else:
                return str(issue)
        
        error_count = sum(1 for issue in issues if get_severity(issue) == 'ERROR')
        warning_count = sum(1 for issue in issues if get_severity(issue) == 'WARNING')
        info_count = len(issues) - error_count - warning_count
        
        logger.info(f"Validation summary: {error_count} errors, {warning_count} warnings, {info_count} info messages")
        
        # Log errors first
        for issue in (i for i in issues if get_severity(i) == 'ERROR'):
            logger.error(f"Validation error: {get_message(issue)}")
            
        # Then warnings
        for issue in (i for i in issues if get_severity(i) == 'WARNING'):
            logger.warning(f"Validation warning: {get_message(issue)}")
            
        # Finally info messages
        for issue in (i for i in issues if get_severity(i) not in ['ERROR', 'WARNING']):
            logger.info(f"Validation info: {get_message(issue)}")

    def _process_household(self, hh_group: pd.DataFrame) -> List[dict]:
        """
        Process a single household and return a list of tax units using vectorized operations.
        
        This method identifies different types of tax units within a household:
        - Married couples (filing jointly or separately)
        - Head of household filers
        - Single filers
        
        Optimized for performance with:
        - Vectorized operations instead of loops
        - Efficient data structures (sets, dictionaries)
        - Batch processing where possible
        
        Args:
            hh_group: DataFrame containing all persons in a household
            
        Returns:
            List of tax unit dictionaries, where each dictionary contains:
            - filer_id: Unique identifier for the tax unit
            - filing_status: One of 'single', 'married_filing_jointly', 
                           'married_filing_separately', 'head_of_household'
            - income: Total income for the tax unit
            - num_dependents: Number of dependents claimed
            - dependents: List of dependent person IDs
            - hh_id: Household identifier
            - primary_filer_id: ID of the primary filer
            - secondary_filer_id: ID of the secondary filer (for joint returns)
        """
        # Skip empty households immediately
        if hh_group.empty:
            logger.debug("Skipping empty household")
            return []

        # Pre-compute household data and cache
        hh_id = hh_group['SERIALNO'].iloc[0]
        logger.info(f"Processing household {hh_id} with {len(hh_group)} members")
        
        # Use vectorized operation to get household data
        hh_data = self.hh_df[self.hh_df['SERIALNO'] == hh_id].iloc[0] if not self.hh_df.empty else {}
        
        # Skip households with zero weight (group quarters, invalid data)
        hh_weight = float(hh_data.get('WGTP', 0))
        if hh_weight <= 0:
            logger.debug(f"Skipping household {hh_id} with zero or negative weight (WGTP={hh_weight})")
            return []
        
        # Vectorized adult identification
        adults_mask = hh_group['AGEP'] >= 18
        adults = hh_group[adults_mask].copy()
        
        if adults.empty:
            logger.debug(f"No adults in household {hh_id}, skipping")
            return []
            
        # Vectorized dependent identification
        logger.debug("Identifying all dependents in household using vectorized operations")
        dependents = identify_dependents(hh_group)
        
        # Convert dependents to a more efficient structure
        dependents = {k: set(v) for k, v in dependents.items()}
        all_dependents = set().union(*dependents.values()) if dependents else set()
        
        # Log initial assignments if debug level
        if logger.isEnabledFor(logging.DEBUG):
            for filer_id, deps in dependents.items():
                if deps:
                    logger.debug(f"Initial dependent assignment - Filer {filer_id} has {len(deps)} dependents")
        
        # Track state with sets for O(1) lookups
        claimed_dependents = set()
        processed_adults = set()
        tax_units = []
        
        # Identify potential joint filers and MFS filers using vectorized operations
        logger.debug("Identifying potential joint and MFS filers")
        joint_filers, mfs_filers = self._identify_joint_filers(adults, hh_group)
        logger.debug(f"Found {len(joint_filers)} joint filer pairs and {len(mfs_filers)} MFS filer pairs")
        
        # Process MFS filers first (they file as single)
        for adult1_id, adult2_id in mfs_filers:
            if adult1_id in processed_adults or adult2_id in processed_adults:
                continue
                
            adult1 = adults.loc[adult1_id]
            adult2 = adults.loc[adult2_id]
            
            logger.info(f"Processing MFS filers: {adult1_id} and {adult2_id}")
            
            # Get available dependents for each adult using set operations
            deps1 = dependents.get(adult1_id, set()) - claimed_dependents
            deps2 = dependents.get(adult2_id, set()) - claimed_dependents
            
            logger.debug(f"  {adult1_id} has {len(deps1)} available dependents")
            logger.debug(f"  {adult2_id} has {len(deps2)} available dependents")
            
            # Create tax units for each MFS filer
            for adult_id, deps in [(adult1_id, deps1), (adult2_id, deps2)]:
                tax_unit = self._create_single_filer(
                    adults.loc[adult_id], 
                    hh_group, 
                    hh_data, 
                    sorted(deps),
                    filing_status='married_filing_separately'
                )
                
                if tax_unit:
                    num_deps = len(tax_unit['dependents'])
                    logger.info(f"  Created MFS tax unit for {adult_id} with {num_deps} dependents")
                    tax_units.append(tax_unit)
                    claimed_dependents.update(tax_unit['dependents'])
            
            # Mark both adults as processed
            processed_adults.update([adult1_id, adult2_id])
        
        # Process joint filers with optimized set operations
        for adult1_id, adult2_id in joint_filers:
            if adult1_id in processed_adults or adult2_id in processed_adults:
                continue
                
            adult1 = adults.loc[adult1_id]
            adult2 = adults.loc[adult2_id]
            
            logger.info(f"Processing joint filers: {adult1_id} and {adult2_id}")
            
            # Get all available dependents using set operations
            deps1 = dependents.get(adult1_id, set())
            deps2 = dependents.get(adult2_id, set())
            available_deps = sorted((deps1 | deps2) - claimed_dependents)
            
            logger.debug(f"  Potential dependents: {len(deps1 | deps2)}, Available: {len(available_deps)}")
            
            # Create joint tax unit
            tax_unit = self._create_joint_filer(adult1, adult2, hh_group, hh_data, available_deps)
            if tax_unit:
                num_deps = len(tax_unit['dependents'])
                logger.info(f"  Created joint tax unit with {num_deps} dependents")
                tax_units.append(tax_unit)
                claimed_dependents.update(tax_unit['dependents'])
                processed_adults.update([adult1_id, adult2_id])
        
        # Process remaining adults as single or head of household filers
        remaining_adult_ids = set(adults.index) - processed_adults
        logger.info(f"Processing {len(remaining_adult_ids)} remaining adults as single/HoH filers")
        
        # Create a list to store potential HoH filers
        potential_hoh = []
        
        # First pass: Identify potential HoH filers. Iterate in ID order: these
        # are sets of person-ID strings, whose order follows PYTHONHASHSEED, and
        # when two adults could claim the same child the first one processed
        # wins -- so set order made tax units (and poverty counts) vary by run.
        for adult_id in sorted(remaining_adult_ids):
            # An adult already claimed as someone's dependent is not a filer.
            if adult_id in claimed_dependents:
                continue
            adult_deps = dependents.get(adult_id, set()) - claimed_dependents
            if adult_deps:  # Only consider adults with unclaimed dependents
                potential_hoh.append((adult_id, adult_deps))

        # Sort potential HoH by number of dependents (most first), ties by ID
        potential_hoh.sort(key=lambda x: (-len(x[1]), x[0]))

        # Process HoH filers
        for adult_id, deps in potential_hoh:
            if adult_id in processed_adults or adult_id in claimed_dependents:
                continue

            tax_unit = self._create_single_filer(
                adults.loc[adult_id],
                hh_group,
                hh_data,
                sorted(deps)
            )

            if tax_unit and tax_unit['filing_status'] == 'head_of_household':
                num_deps = len(tax_unit['dependents'])
                logger.info(f"Created HoH tax unit for {adult_id} with {num_deps} dependents")
                tax_units.append(tax_unit)
                claimed_dependents.update(tax_unit['dependents'])
                processed_adults.add(adult_id)

        # Process remaining adults as single filers.
        #
        # An adult designated as another adult's dependent (e.g. an adult child
        # still at home, or a qualifying relative) must be CLAIMED, not filed —
        # but only if their guardian actually files and claims them. Process
        # likely guardians first (non-dependents, most-dependents-first) so the
        # claim lands before we reach the dependent, and skip anyone already
        # claimed. Any designated dependent left unclaimed (guardian couldn't
        # claim them) falls through to filing for themselves, so no person is
        # dropped from the population.
        remaining_adult_ids = set(adults.index) - processed_adults
        designated_adult_deps = {a for a in remaining_adult_ids if a in all_dependents}

        def _filer_order_key(aid: str):
            is_designated = aid in designated_adult_deps
            n_deps = len(dependents.get(aid, set()))
            return (is_designated, -n_deps, aid)     # ID breaks ties deterministically

        for adult_id in sorted(remaining_adult_ids, key=_filer_order_key):
            if adult_id in processed_adults or adult_id in claimed_dependents:
                continue
            adult = adults.loc[adult_id]
            deps = sorted(dependents.get(adult_id, set()) - claimed_dependents)

            # Don't force 'single' status - let _create_single_filer determine HoH eligibility
            tax_unit = self._create_single_filer(
                adult,
                hh_group,
                hh_data,
                deps
            )

            if tax_unit:
                num_deps = len(tax_unit['dependents'])
                logger.debug(f"Created single filer tax unit for {adult_id} with {num_deps} dependents")
                tax_units.append(tax_unit)
                claimed_dependents.update(tax_unit['dependents'])
                processed_adults.add(adult_id)
        
        # Assign any remaining unclaimed dependents
        unclaimed_deps = all_dependents - claimed_dependents
        
        if unclaimed_deps:
            logger.warning(f"Household {hh_id} has {len(unclaimed_deps)} unassigned dependents")
            
            # Create a list of (tax_unit_idx, dependent_id) pairs that can be claimed
            assignments = []
            
            for dep_id in sorted(unclaimed_deps):
                dependent = hh_group.loc[dep_id]
                
                # Find the first tax unit that can claim this dependent
                for idx, tax_unit in enumerate(tax_units):
                    if self._can_claim_dependent(tax_unit, dependent, hh_group):
                        assignments.append((idx, dep_id))
                        break
            
            # Apply all valid assignments
            for idx, dep_id in assignments:
                tax_units[idx]['dependents'].append(dep_id)
                tax_units[idx]['num_dependents'] += 1
                claimed_dependents.add(dep_id)
                logger.info(f"Assigned unclaimed dependent {dep_id} to tax unit {tax_units[idx]['filer_id']}")
        
        # Final validation and logging. Adults claimed as a dependent of another
        # filer are correctly assigned (as dependents), not unassigned filers.
        unassigned_adults = set(adults.index) - processed_adults - claimed_dependents
        num_assigned_deps = len(claimed_dependents)
        
        # Enforce maximum tax units per household (if cap is set)
        if self.MAX_TAX_UNITS_PER_HOUSEHOLD is not None and len(tax_units) > self.MAX_TAX_UNITS_PER_HOUSEHOLD:
            # Sort tax units by priority: couples (joint/MFS) > head_of_household > single
            # CRITICAL: Prioritize keeping couple units to avoid losing legitimate married couples
            def get_priority(tax_unit):
                status = tax_unit.get('filing_status', '')
                # Priority 0: Married couples (joint or MFS) - must keep these!
                if status in ['married_filing_jointly', 'married_filing_separately']:
                    return 0
                # Priority 1: Head of household (has dependents)
                elif status == 'head_of_household':
                    return 1
                # Priority 2: Single filers (lowest priority)
                else:
                    return 2
            
            # Sort by priority and then by number of dependents (descending)
            tax_units.sort(key=lambda x: (get_priority(x), -x.get('num_dependents', 0)))
            
            # Keep only the first MAX_TAX_UNITS_PER_HOUSEHOLD tax units
            removed_units = tax_units[self.MAX_TAX_UNITS_PER_HOUSEHOLD:]
            tax_units = tax_units[:self.MAX_TAX_UNITS_PER_HOUSEHOLD]
            
            # Log a warning about the removed tax units
            logger.warning(
                f"Household {hh_id} had {len(tax_units) + len(removed_units)} tax units, "
                f"capped at {self.MAX_TAX_UNITS_PER_HOUSEHOLD}. "
                f"Removed {len(removed_units)} tax units: {[u['filing_status'] for u in removed_units]}"
            )
        
        max_cap_str = "unlimited" if self.MAX_TAX_UNITS_PER_HOUSEHOLD is None else str(self.MAX_TAX_UNITS_PER_HOUSEHOLD)
        logger.info(
            f"Household {hh_id} summary: "
            f"{len(tax_units)} tax units (max {max_cap_str}), "
            f"{num_assigned_deps}/{len(all_dependents)} dependents assigned, "
            f"{len(unassigned_adults)}/{len(adults)} adults unassigned"
        )
        
        if unassigned_adults:
            logger.warning(f"  WARNING: {len(unassigned_adults)} adults not assigned to any tax unit")
        
        return tax_units
    
    def _identify_joint_filers(self, adults: pd.DataFrame, hh_members: pd.DataFrame) -> Tuple[List[Tuple[str, str]], List[Tuple[str, str]]]:
        """
        Identify potential joint filers and married filing separately couples within a household.
        
        Uses strict RELSHIPP validation first, then fallback relaxed validation for missed couples.
        
        Args:
            adults: DataFrame of adult household members
            hh_members: All members of the household
            
        Returns:
            Tuple of (joint_filers, mfs_filers) where each is a list of (person_id1, person_id2) tuples
        """
        from tax_modeler.units.status.mfj import _are_married
        
        joint_filers = []
        mfs_filers = []
        processed = set()
        
        # Create a list of all adult IDs
        adult_ids = list(adults.index)
        
        # PHASE 1: Find all married couples using strict RELSHIPP validation
        logger.debug("Phase 1: Identifying married couples using strict RELSHIPP validation")
        for i in range(len(adult_ids)):
            id1 = adult_ids[i]
            if id1 in processed:
                continue
                
            person1 = adults.loc[id1]
            
            # Only consider married adults
            if person1.get('MAR') != 1:
                continue
                
            # Look for spouse among remaining adults
            for j in range(i+1, len(adult_ids)):
                id2 = adult_ids[j]
                if id2 in processed:
                    continue
                    
                person2 = adults.loc[id2]
                
                # Use strict validation: must be householder + spouse
                if _are_married(person1, person2):
                    logger.debug(f"Found married couple (strict): {id1} (relshipp {person1.get('RELSHIPP')}) and "
                                f"{id2} (relshipp {person2.get('RELSHIPP')})")
                    
                    # Check if they should file separately
                    if self._should_file_separately(person1, person2, hh_members):
                        mfs_filers.append((id1, id2))
                        logger.debug(f"  Identified MFS filers: {id1} and {id2}")
                    else:
                        joint_filers.append((id1, id2))
                        logger.debug(f"  Identified joint filers: {id1} and {id2}")
                    
                    processed.update([id1, id2])
                    break  # Move to next person after finding a match
        
        # PHASE 2: Fallback validation for married couples missed by strict RELSHIPP check
        logger.debug("Phase 2: Identifying married couples using relaxed validation (fallback)")
        remaining_married = [aid for aid in adult_ids 
                            if aid not in processed 
                            and adults.loc[aid].get('MAR') == 1]
        
        for i in range(len(remaining_married)):
            id1 = remaining_married[i]
            if id1 in processed:
                continue
            
            person1 = adults.loc[id1]
            
            # Try to pair with another remaining married adult
            for j in range(i+1, len(remaining_married)):
                id2 = remaining_married[j]
                if id2 in processed:
                    continue
                
                person2 = adults.loc[id2]
                
                # Use relaxed validation
                if self._could_be_married_couple(person1, person2):
                    logger.debug(f"Found married couple (fallback): {id1} (relshipp {person1.get('RELSHIPP')}) and "
                                f"{id2} (relshipp {person2.get('RELSHIPP')})")
                    
                    # Check if they should file separately
                    if self._should_file_separately(person1, person2, hh_members):
                        mfs_filers.append((id1, id2))
                        logger.debug(f"  Identified MFS filers (fallback): {id1} and {id2}")
                    else:
                        joint_filers.append((id1, id2))
                        logger.debug(f"  Identified joint filers (fallback): {id1} and {id2}")
                    
                    processed.update([id1, id2])
                    break  # Move to next person after finding a match
        
        # PHASE 3: Disabled - was creating false positives
        # The fallback detection (Phase 2) with relaxed age is sufficient
        
        logger.info(f"Identified {len(joint_filers)} joint filer pairs and {len(mfs_filers)} MFS pairs in household {hh_members['SERIALNO'].iloc[0] if not hh_members.empty else 'unknown'} (Phase 1 & 2 only)")
        return joint_filers, mfs_filers

    def _could_be_married_couple(self, person1: pd.Series, person2: pd.Series) -> bool:
        """
        Relaxed validation for married couples missed by strict RELSHIPP check.
        Uses multiple signals to identify likely married couples (Options 1&2).
        
        Implements:
        - Option 1: Fallback detection with relaxed criteria (opposite sex, age ≤20 years, citizenship)
        - Option 2: Increased MFS probabilities (in _should_file_separately)
        
        Args:
            person1: First person
            person2: Second person
            
        Returns:
            bool: True if they could be a married couple
        """
        # Both must be marked as married (MAR=1)
        if person1.get('MAR') != 1 or person2.get('MAR') != 1:
            return False
        
        # Opposite sex (SEX: 1=male, 2=female)
        sex1 = person1.get('SEX')
        sex2 = person2.get('SEX')
        if sex1 == sex2 or sex1 is None or sex2 is None:
            return False
        
        # Age similarity (≤20 years)
        age1 = person1.get('AGEP', 0)
        age2 = person2.get('AGEP', 0)
        age_diff = abs(age1 - age2)
        if age_diff > 20:
            return False
        
        # Both must be citizens/residents (CIT: 1-4 are eligible)
        cit1 = person1.get('CIT', 1)
        cit2 = person2.get('CIT', 1)
        if cit1 > 4 or cit2 > 4:
            return False
        
        # No additional income/employment criteria - keep simple
        # The age and sex checks are sufficient for fallback detection
        
        logger.debug(f"Relaxed validation passed (Option F): {person1.name} (age {age1}, sex {sex1}) and "
                    f"{person2.name} (age {age2}, sex {sex2})")
        return True
    
    def _should_file_separately(self, adult1: pd.Series, adult2: pd.Series, 
                              hh_members: pd.DataFrame) -> bool:
        """
        Determine if a married couple should file separately using multi-factor scoring.
        
        PRIORITY 1 FIX: Restrict MFS to high-income couples only (AGI > $150K)
        to match DOTAX average AGI of $196,726 for MFS filers.
        
        Args:
            adult1: First spouse
            adult2: Second spouse
            hh_members: All household members
            
        Returns:
            bool: True if should file separately, False otherwise
        """
        # Get basic info
        income1 = float(adult1.get('PINCP', 0) or 0)
        income2 = float(adult2.get('PINCP', 0) or 0)
        age1 = int(adult1.get('AGEP', 30))
        age2 = int(adult2.get('AGEP', 30))
        rel1 = adult1.get('RELSHIPP', 0)
        rel2 = adult2.get('RELSHIPP', 0)
        
        # PRIORITY 1 FIX: Only allow MFS for high-income couples
        # DOTAX data shows MFS filers have avg AGI of $196,726 (vs $45,702 in current model)
        # This indicates MFS is primarily used by high-income couples for tax optimization
        total_income = income1 + income2
        
        # Minimum income threshold for MFS eligibility
        MIN_MFS_INCOME = 150000  # $150K combined income minimum
        
        if total_income < MIN_MFS_INCOME:
            # Low-income couples should always file jointly
            return False
        
        # Calculate MFS likelihood score based on multiple factors
        mfs_score = 0
        
        # Factor 1: Income disparity (strongest predictor)
        if income1 > 0 and income2 > 0:
            ratio = max(income1, income2) / min(income1, income2)
            if ratio > 20:
                mfs_score += 4  # Very large disparity
            elif ratio > 10:
                mfs_score += 3  # Large disparity
            elif ratio > 5:
                mfs_score += 2  # Moderate disparity
            elif ratio > 3:
                mfs_score += 1  # Small disparity
        
        # Factor 2: High-income/low-income pattern
        if ((income1 > 100000 and income2 < 10000) or 
            (income2 > 100000 and income1 < 10000)):
            mfs_score += 3
        
        # Factor 3: Age factors
        avg_age = (age1 + age2) / 2
        age_diff = abs(age1 - age2)
        
        if avg_age < 25:  # Very young couples
            mfs_score += 2
        elif avg_age < 30:  # Young couples
            mfs_score += 1
            
        if age_diff > 15:  # Large age gap
            mfs_score += 1
        
        # Factor 4: Dual high earners (tax optimization)
        if income1 > 50000 and income2 > 50000:
            mfs_score += 1
        
        # Factor 5: Non-traditional couple structure
        is_householder_spouse = ((rel1 == 20 and rel2 == 21) or 
                                (rel1 == 21 and rel2 == 20))
        if not is_householder_spouse:
            mfs_score += 1
        
        # Factor 6: Complex household (multiple adults)
        num_adults = len(hh_members[hh_members['AGEP'] >= 18])
        if num_adults > 2:
            mfs_score += 1
        
        # Factor 7: Very high total income (tax planning)
        # Enhanced scoring for ultra-high-income couples
        if total_income > 500000:
            mfs_score += 3  # Ultra-high income: very likely MFS
        elif total_income > 300000:
            mfs_score += 2  # Very high income: likely MFS
        elif total_income > 200000:
            mfs_score += 1  # High income: possible MFS
        
        # Determine MFS threshold based on analysis
        # Target: 2.5% of married couples should file MFS (DOTAX 2022 resident benchmark)
        # PRIORITY 1 FIX: Income-based probability for high-income couples only
        
        should_file_separately = False
        
        # Income-based MFS probability (higher income = higher probability)
        # This ensures MFS filers have high average AGI matching DOTAX ($196,726)
        income_based_probability = 0.0
        
        if total_income > 500000:
            income_based_probability = 0.20  # 20% for ultra-high income
        elif total_income > 400000:
            income_based_probability = 0.15  # 15% for very high income
        elif total_income > 300000:
            income_based_probability = 0.10  # 10% for high income
        elif total_income > 200000:
            income_based_probability = 0.05  # 5% for upper-middle income
        else:
            income_based_probability = 0.02  # 2% for $150K-$200K
        
        if mfs_score >= 8:
            # Extremely high score: definitely file separately
            should_file_separately = True
            reason = f"extremely high MFS score ({mfs_score})"
        elif mfs_score >= 6:
            # High score: use income-based probability with boost
            serialno = str(adult1.get('SERIALNO', '0'))
            sporder1 = str(adult1.get('SPORDER', 0))
            sporder2 = str(adult2.get('SPORDER', 1))
            seed_string = f"{serialno}_{sporder1}_{sporder2}_mfs_score{mfs_score}"
            
            import hashlib
            seed = int(hashlib.md5(seed_string.encode()).hexdigest()[:8], 16)
            import random
            random.seed(seed)
            
            # Boost probability for high scores
            boosted_probability = min(0.90, income_based_probability * (1 + mfs_score / 10))
            should_file_separately = random.random() < boosted_probability
            reason = f"high MFS score ({mfs_score}), income-based prob: {boosted_probability:.2f}"
        elif mfs_score >= 4:
            # Medium score: use income-based probability
            serialno = str(adult1.get('SERIALNO', '0'))
            sporder1 = str(adult1.get('SPORDER', 0))
            sporder2 = str(adult2.get('SPORDER', 1))
            seed_string = f"{serialno}_{sporder1}_{sporder2}_mfs_score{mfs_score}"
            
            import hashlib
            seed = int(hashlib.md5(seed_string.encode()).hexdigest()[:8], 16)
            import random
            random.seed(seed)
            
            should_file_separately = random.random() < income_based_probability
            reason = f"medium MFS score ({mfs_score}), income-based prob: {income_based_probability:.2f}"
        elif mfs_score == 3:
            # Low-medium score: file separately occasionally
            serialno = str(adult1.get('SERIALNO', '0'))
            sporder1 = str(adult1.get('SPORDER', 0))
            sporder2 = str(adult2.get('SPORDER', 1))
            seed_string = f"{serialno}_{sporder1}_{sporder2}_mfs_score3"
            
            import hashlib
            seed = int(hashlib.md5(seed_string.encode()).hexdigest()[:8], 16)
            import random
            random.seed(seed)
            
            # File separately ~5% of the time for score 3 couples (increased from 2%)
            should_file_separately = random.random() < 0.05
            reason = f"low-medium MFS score ({mfs_score}), random: {should_file_separately}"
        
        if not should_file_separately:
            reason = f"low MFS score ({mfs_score}), filing jointly"
        
        logger.debug(f"MFS decision for adults {adult1.name} and {adult2.name}: {reason}")
        return should_file_separately
        
    def _calculate_income(self, person: pd.Series) -> float:
        """
        Calculate total income for a person, adjusted by ADJINC factor.
        
        Args:
            person: Series containing person's data with income fields
            
        Returns:
            float: Total income adjusted by ADJINC factor
        """
        # Get ADJINC factor (default to 1.0 if not present)
        # CRITICAL: ADJINC in PUMS is stored as integer (e.g., 1184371 = 1.184371)
        # Must divide by 1,000,000 to get the actual adjustment factor
        adjinc_raw = float(person.get('ADJINC', 1000000))
        adjinc = adjinc_raw / 1000000.0  # Convert from integer to decimal factor
        
        # Use PINCP (total person income) which already includes all income sources
        # This avoids double-counting that would occur if we summed individual components
        total_income = float(person.get('PINCP', 0) or 0)
        
        # Apply ADJINC adjustment
        total_income *= adjinc
        
        return total_income
        
    # NOTE: _calculate_hybrid_weight method is defined later in the file (line ~1164)
    # to avoid duplication. See that method for weight calculation logic.

    def _create_joint_filer(self, adult1: pd.Series, adult2: pd.Series, 
                           hh_members: pd.DataFrame, hh_data: pd.Series, 
                           available_deps: List[str] = None) -> Optional[dict]:
        """
        Create a tax unit for a joint filer.
        
        Args:
            adult1: First adult in the joint filing couple
            adult2: Second adult in the joint filing couple
            hh_members: All members of the household
            hh_data: Household-level data
            available_deps: List of available dependent person_ids that can be claimed
            
        Returns:
            Dictionary containing tax unit information or None if not valid
        """
        if available_deps is None:
            available_deps = []
            
        logger.debug(f"Creating joint filer tax unit for {adult1.name} and {adult2.name} with {len(available_deps)} available dependents")
        
        # Filter available dependents to only include those actually in the household
        valid_dependents = [d for d in available_deps if d in hh_members.index]
        
        # Combine all members for income calculation and collect person weights
        members_to_include = [adult1, adult2]
        person_weights = [adult1.get('PWGTP', 1.0), adult2.get('PWGTP', 1.0)]
        
        if valid_dependents:
            for dep_id in valid_dependents:
                dep = hh_members.loc[dep_id]
                members_to_include.append(dep)
                person_weights.append(dep.get('PWGTP', 1.0))
        
        # Tax-unit AGI is the FILERS' income only (the two spouses) — a
        # dependent's earnings are not the filers' AGI (audit B5). person_weights
        # above still includes dependents, so the unit's weight basis
        # (household-vs-person, used by _calculate_hybrid_weight /
        # _compute_replicate_weights) is unchanged; only the income sum drops them.
        income_df = pd.DataFrame([adult1, adult2])
        # Disable 2026 growth projection for SOI 2022 comparison
        income = calculate_tax_unit_income(income_df, apply_2026_growth=False)

        # Calculate hybrid weight
        hh_weight = float(hh_data.get('WGTP', 1.0))
        hybrid_weight = self._calculate_hybrid_weight(hh_weight, person_weights, 'married_filing_jointly')

        # Extract per-filer income components for source-specific growth projections
        p1 = extract_person_income_components(adult1)
        p2 = extract_person_income_components(adult2)

        # Create tax unit with proper string IDs and include hybrid weight and geographic info
        tax_unit = {
            'filer_id': f"{hh_data['SERIALNO']}_joint_{adult1.name}_{adult2.name}",
            'SERIALNO': str(hh_data['SERIALNO']),  # Ensure SERIALNO is string
            'filing_status': 'married_filing_jointly',
            'primary_filer_id': str(adult1.name),   # Convert to string
            'secondary_filer_id': str(adult2.name), # Convert to string
            'income': income,
            'num_dependents': len(valid_dependents),
            'dependents': [str(d) for d in valid_dependents],  # Ensure dependents are strings
            'dependents_details': self._build_dependent_details(valid_dependents, hh_members),
            'hh_id': str(adult1['SERIALNO']),  # Ensure hh_id is string
            'weight': hybrid_weight,  # Use hybrid weight
            'hh_weight': hh_weight,   # Store original household weight for reference
            'person_weight_sum': sum(person_weights),  # Store sum of person weights for reference
            'PUMA': hh_data.get('PUMA'),  # Add PUMA code for geographic analysis
            'PUMA10': hh_data.get('PUMA10', hh_data.get('PUMA')),  # Handle both PUMA and PUMA10 fields
            # Primary filer income components
            'primary_wagp': p1['wagp'], 'primary_semp': p1['semp'],
            'primary_intp': p1['intp'], 'primary_div': p1['div'],
            'primary_retp': p1['retp'], 'primary_ssp': p1['ssp'],
            'primary_ssp_full': p1['ssp_full'], 'primary_ssip': p1['ssip'],
            'primary_pap': p1['pap'],
            'primary_oip': p1['oip'], 'primary_agep': p1['agep'],
            # Secondary filer income components
            'secondary_wagp': p2['wagp'], 'secondary_semp': p2['semp'],
            'secondary_intp': p2['intp'], 'secondary_div': p2['div'],
            'secondary_retp': p2['retp'], 'secondary_ssp': p2['ssp'],
            'secondary_ssp_full': p2['ssp_full'], 'secondary_ssip': p2['ssip'],
            'secondary_pap': p2['pap'],
            'secondary_oip': p2['oip'], 'secondary_agep': p2['agep'],
        }
        # PUMS replicate weights for SDR variance: weight_r01..weight_r80
        # (empty dict if include_replicate_weights=False or replicates absent).
        # n_members = len(person_weights) so the WGTP/PWGTP choice matches
        # the main weight (joint is always >= 2 members -> WGTPr).
        tax_unit.update(self._compute_replicate_weights(
            hh_data, adult1, 'married_filing_jointly', len(person_weights),
        ))

        logger.debug(f"Created joint tax unit: {tax_unit}")
        return tax_unit

    def _calculate_income(self, person: pd.Series) -> float:
        """
        Calculate total income for a person, adjusted by ADJINC factor.

        Args:
            person: Series containing person's data with income fields

        Returns:
            float: Total income adjusted by ADJINC factor
        """
        # Get ADJINC factor (default to 1.0 if not present)
        # ADJINC values in PUMS data are already the adjustment factors (around 1.0-1.2)
        adjinc = float(person.get('ADJINC', 1.0))

        # Use PINCP (total person income) which already includes all income sources
        # This avoids double-counting that would occur if we summed individual components
        total_income = float(person.get('PINCP', 0) or 0)

        # Apply ADJINC adjustment
        total_income *= adjinc

        return total_income
        
    def _calculate_hybrid_weight(self, hh_weight: float, person_weights: List[float], 
                               filing_status: str) -> float:
        """
        Calculate hybrid weight using person weights only (not household weight).
        
        Args:
            hh_weight: Household weight (WGTP) - not used, kept for compatibility
            person_weights: List of person weights (PWGTP) for tax unit members
            filing_status: Tax filing status
            
        Returns:
            float: Weight for the tax unit
        """
        # FIXED: Use person weights only
        # The old formula (hh_weight + sum(person_weights))/2 was inflating weights
        # Person weight (PWGTP) already represents population extrapolation
        
        if len(person_weights) == 0:
            # Fallback to household weight if no person weights
            hybrid_weight = hh_weight
        elif len(person_weights) == 1:
            # Single person: use their person weight
            hybrid_weight = person_weights[0]
        else:
            # Multiple people in tax unit (joint/MFS): use household weight
            # PWGTP is per-person; for a tax unit, use WGTP (household weight)
            # which represents the couple as a single tax filing unit
            hybrid_weight = hh_weight
        
        # Apply calibration factors to match DOTAX TY2022 filing-status shares
        # (single 52.8% / MFJ 34.1% / HoH 10.6% / MFS 2.5%, normalized MFJ=1.0).
        # Re-derived 2026-06 after the B2-B5 construction fixes (SCH enrollment,
        # canonical HoH RELSHIPP codes, 18-23 dependent support test, filer-only
        # AGI) changed the natural mix: HoH dropped 1.88->1.30 (it no longer
        # over-counts via mis-decoded partner/roommate codes), MFS rose
        # 1.05->1.35, single 0.85->0.82. See scripts/diagnose_filing_status.py.
        calibration_factors = {
            'single': 0.82,
            'married_filing_jointly': 1.0,
            'head_of_household': 1.30,
            'married_filing_separately': 1.35,
        }

        adjustment = calibration_factors.get(filing_status, 1.0)
        calibrated_weight = hybrid_weight * adjustment

        return max(calibrated_weight, 0.1)  # Ensure weight is never zero or negative

    def _compute_replicate_weights(
        self,
        hh_data: pd.Series,
        primary_person: pd.Series,
        filing_status: str,
        n_members: int,
    ) -> Dict[str, float]:
        """Per-replicate tax-unit weight dict, mirroring _calculate_hybrid_weight.

        For each of WGTP1..WGTP80, compute the same hybrid-weight formula
        the main `weight` column uses, applied to the replicate household
        weight (and the corresponding PWGTPr for one-person tax units).
        Returns a dict ``{"weight_r01": float, ..., "weight_r80": float}``
        ready to merge into the tax-unit row.

        Empty dict (no replicate keys added) if:
          * ``include_replicate_weights=False`` was passed to __init__, or
          * The PUMS input frames lack the WGTP1..WGTP80 / PWGTP1..PWGTP80
            columns entirely (e.g., synthetic fixtures).

        Notes
        -----
        The WGTP-vs-PWGTP choice MUST track ``_calculate_hybrid_weight``
        exactly: that method keys on the *member count* of the tax unit
        (1 member ⇒ the person's PWGTP; ≥2 members ⇒ the household WGTP),
        **not** on filing status. A single/HoH filer *with dependents* has
        ≥2 members and therefore uses WGTP for its main weight, so its
        replicates must use WGTPr too — keying on filing status instead
        (the prior bug) used PWGTPr there and biased every SDR standard
        error for HoH and single-with-dependent units. ``n_members`` is
        ``len(person_weights)`` from the caller.

        The same per-filing-status calibration factor that scales the
        main weight (single 0.82, MFJ 1.0, HoH 1.30, MFS 1.35) is
        applied to each replicate, so SDR ratios at the tax-unit level
        are dimensionally consistent with the main weight. Keep this dict in
        lockstep with _calculate_hybrid_weight.
        """
        if not self._wgtp_replicate_cols:
            return {}

        calibration_factors = {
            'single': 0.82,
            'married_filing_jointly': 1.0,
            'head_of_household': 1.30,
            'married_filing_separately': 1.35,
        }
        adjustment = calibration_factors.get(filing_status, 1.0)
        # Mirror _calculate_hybrid_weight: only a *one-person* tax unit uses
        # the per-person weight; 0 or ≥2 members use the household weight.
        use_person_weight = n_members == 1

        out: Dict[str, float] = {}
        for r_idx, wgtp_col in enumerate(self._wgtp_replicate_cols, start=1):
            hh_w_r = float(hh_data.get(wgtp_col, 1.0))
            if use_person_weight:
                # One-person tax unit: per-replicate person weight when
                # available, else fall back to the household replicate.
                pwgtp_col = f"PWGTP{r_idx}"
                if pwgtp_col in primary_person.index:
                    base = float(primary_person.get(pwgtp_col, 1.0))
                else:
                    base = hh_w_r
            else:
                # Multi-person tax unit (joint, or single/HoH/MFS with
                # dependents): use the household replicate weight, matching
                # _calculate_hybrid_weight returning hh_weight when
                # len(person_weights) >= 2.
                base = hh_w_r
            calibrated = max(base * adjustment, 0.1)
            out[f"weight_r{r_idx:02d}"] = calibrated
        return out

    def _can_claim_dependent(self, tax_unit: dict, dependent: pd.Series, hh_members: pd.DataFrame) -> bool:
        """
        Check if a tax unit can claim a dependent.

        Args:
            tax_unit: The tax unit dictionary
            dependent: The dependent's data as a Series
            hh_members: All household members

        Returns:
            bool: True if the tax unit can claim the dependent
        """
        # Get the primary filer - handle missing primary_filer_id
        primary_filer_id = tax_unit.get('primary_filer_id')
        if not primary_filer_id or primary_filer_id not in hh_members.index:
            logger.warning(f"Primary filer ID {primary_filer_id} not found in household members")
            return False
            
        primary_filer = hh_members.loc[primary_filer_id]
        
        # Check relationship using correct PUMS codes
        rel_to_primary = dependent.get('RELSHIPP', 0)
        
        # Direct child/stepchild/adopted child relationship
        # PUMS codes: 22=Biological child, 23=Adopted child, 24=Stepchild
        if rel_to_primary in [22, 23, 24]:  
            return True
            
        # Grandchild relationship
        if rel_to_primary == 25:  # Grandchild
            return True
            
        # Other qualifying relative relationships
        if rel_to_primary in [26, 27, 28, 29, 30]:  # Siblings, parents, grandparents, in-laws, other relatives
            # Check if they lived together all year
            # (In PUMS, if they're in the same household, we assume they lived together all year)
            return True
            
        return False

    @staticmethod
    def _build_dependent_details(dep_ids: List[str], hh_members: pd.DataFrame) -> List[dict]:
        """Build per-dependent records (real age/relationship/citizenship/etc.).

        Credit code (EITC ``_count_qualifying_children_eitc`` and CTC
        ``_is_qualifying_child_ctc``) applies its OWN age/relationship tests on
        these dicts. Carrying real PUMS values — instead of synthetic age-10
        placeholders — is what lets an attached adult dependent (e.g. an elderly
        parent) be excluded from EITC/CTC qualifying-child counts.
        """
        def _safe_int(value, default=0):
            if value is None or (isinstance(value, float) and pd.isna(value)):
                return default
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        details: List[dict] = []
        for dep_id in dep_ids:
            dep_id_str = str(dep_id)
            if dep_id_str not in hh_members.index:
                continue
            d = hh_members.loc[dep_id_str]
            _pw = d.get('PWGTP', 0.0)
            try:
                _pw = float(_pw) if _pw is not None and not pd.isna(_pw) else 0.0
            except (TypeError, ValueError):
                _pw = 0.0
            details.append({
                'age': _safe_int(d.get('AGEP', 0)),
                'relationship': _safe_int(d.get('RELSHIPP', 0)),
                'citizenship': _safe_int(d.get('CIT', 1), default=1),
                'months_in_home': 12,  # PUMS has no sub-year residency data
                'school_level': _safe_int(d.get('SCHL', 0)),
                'disabled': _safe_int(d.get('DIS', 2), default=2) == 1,
                # The dependent's OWN person weight. The tax unit's `weight` is a
                # filing-status-calibrated hybrid (WGTP x DOTAX share factors) —
                # correct for revenue, WRONG for counting people. Consumers that
                # need a demographic headcount (e.g. the RxKids birth driver)
                # must weight by this, not by the unit weight.
                'pwgtp': _pw,
            })
        return details

    def _create_single_filer(self, adult: pd.Series, hh_members: pd.DataFrame,
                           hh_data: pd.Series, available_deps: List[str] = None,
                           filing_status: str = None) -> Optional[dict]:
        """
        Create a tax unit for a single filer.
        
        Args:
            adult: The adult who is the primary filer
            hh_members: All members of the household
            hh_data: Household-level data
            available_deps: List of available dependent person_ids that can be claimed
            filing_status: Explicit filing status to use (overrides automatic determination)
            
        Returns:
            Dictionary containing tax unit information or None if not valid
        """
        if available_deps is None:
            available_deps = []
        
        # Skip if this adult is already in a tax unit
        if 'in_tax_unit' in adult and adult['in_tax_unit']:
            logger.debug(f"Adult {adult.name} is already in a tax unit, skipping")
            return None
            
        # Filter available dependents to only include those actually in the household
        # and not already in another tax unit
        valid_dependents = []
        logger.debug(f"Processing available_deps: {available_deps}")
        logger.debug(f"Household members index: {list(hh_members.index)}")
        
        for dep_id in available_deps:
            # Ensure dep_id is a string
            dep_id_str = str(dep_id)
            logger.debug(f"Checking dependent ID: {dep_id} (type: {type(dep_id)}) -> {dep_id_str}")
            
            if dep_id_str in hh_members.index:
                dep = hh_members.loc[dep_id_str]
                if 'in_tax_unit' not in dep or not dep['in_tax_unit']:
                    valid_dependents.append(dep_id_str)
                    logger.debug(f"Added valid dependent: {dep_id_str}")
            else:
                logger.warning(f"Dependent ID {dep_id_str} not found in household members index")
        
        # Calculate income and collect person weights (include dependents in the calculation)
        members_to_include = []
        person_weights = []
        
        # Add the adult
        if isinstance(adult, pd.Series):
            members_to_include.append(adult)
            person_weights.append(adult.get('PWGTP', 1.0))
        else:
            members_to_include.append(adult)
            person_weights.append(1.0)  # Default weight if not available
        
        # Add dependents if any
        if valid_dependents:
            for dep_id in valid_dependents:
                try:
                    dep_data = hh_members.loc[dep_id]
                    members_to_include.append(dep_data)
                    person_weights.append(dep_data.get('PWGTP', 1.0))
                except KeyError as e:
                    logger.error(f"Error accessing dependent {dep_id}: {e}")
                    logger.error(f"Available household member IDs: {list(hh_members.index)}")
                    continue
        
        # Calculate income — FILER ONLY. A dependent's earnings are not the
        # filer's AGI (audit B5). person_weights above still includes dependents
        # so the unit's weight basis is unchanged; only the income sum drops them.
        try:
            adult_row = adult.to_dict() if hasattr(adult, 'to_dict') else adult
            income_df = pd.DataFrame([adult_row])
            # Disable 2026 growth projection for SOI 2022 comparison
            income = calculate_tax_unit_income(income_df, apply_2026_growth=False)
        except Exception as e:
            logger.error(f"Error calculating income for tax unit: {e}")
            income = 0.0
        
        # NOTE: Filing threshold check removed to match SOI data universe
        # SOI includes ALL filers, even those below standard filing thresholds, because:
        # - Many file for refundable credits (EITC, CTC, etc.)
        # - Many file to claim tax refunds from withholding
        # - Self-employed with income ≥$400 must file
        # - Various other filing requirements (household employment tax, etc.)
        # 
        # Removing this check captures ~155,000 additional low-income filers,
        # improving alignment with SOI benchmarks from 3% to ~98% coverage
        # in the under-$5K income brackets.
        
        if filing_status is None:
            # Determine filing status based on marital status and household role
            # Note: Married adults (MAR=1) who are NOT householder/spouse (RELSHIPP 20/21)
            # are living separately from their spouse and can be "considered unmarried"
            # for tax purposes, allowing them to file as Single or HoH
            filing_status = 'single'
            
            person_data = hh_members.copy()
            person_data['SERIALNO'] = hh_data.get('SERIALNO', '')
            
            # Check if this person has dependents in their tax unit
            has_dependents = len(valid_dependents) > 0
            
            if is_head_of_household(adult, person_data, has_dependents=has_dependents):
                filing_status = 'head_of_household'
                logger.debug(f"Person {adult.name} qualifies as Head of Household with {len(valid_dependents)} dependents")
        
        # Calculate hybrid weight
        hh_weight = float(hh_data.get('WGTP', 1.0))
        hybrid_weight = self._calculate_hybrid_weight(hh_weight, person_weights, filing_status)

        # Extract primary filer income components for source-specific growth projections
        p1 = extract_person_income_components(adult)

        # Create tax unit with hybrid weight and geographic info
        tax_unit = {
            'filer_id': f"{hh_data.get('SERIALNO', '')}_{filing_status}_{adult.name}",
            'SERIALNO': str(hh_data.get('SERIALNO', '')),
            'filing_status': filing_status,
            'primary_filer_id': str(adult.name),
            'income': income,
            'num_dependents': len(valid_dependents),
            'dependents': [str(d) for d in valid_dependents],
            'dependents_details': self._build_dependent_details(valid_dependents, hh_members),
            'hh_id': str(adult.get('SERIALNO', hh_data.get('SERIALNO', ''))),
            'weight': hybrid_weight,
            'hh_weight': hh_weight,
            'person_weight_sum': sum(person_weights),
            'PUMA': hh_data.get('PUMA'),  # Add PUMA code for geographic analysis
            'PUMA10': hh_data.get('PUMA10', hh_data.get('PUMA')),  # Handle both PUMA and PUMA10 fields
            # Primary filer income components
            'primary_wagp': p1['wagp'], 'primary_semp': p1['semp'],
            'primary_intp': p1['intp'], 'primary_div': p1['div'],
            'primary_retp': p1['retp'], 'primary_ssp': p1['ssp'],
            'primary_ssp_full': p1['ssp_full'], 'primary_ssip': p1['ssip'],
            'primary_pap': p1['pap'],
            'primary_oip': p1['oip'], 'primary_agep': p1['agep'],
            # Secondary filer (none for single filers — zero for uniform schema)
            'secondary_wagp': 0.0, 'secondary_semp': 0.0,
            'secondary_intp': 0.0, 'secondary_div': 0.0,
            'secondary_retp': 0.0, 'secondary_ssp': 0.0,
            'secondary_ssp_full': 0.0, 'secondary_ssip': 0.0,
            'secondary_pap': 0.0,
            'secondary_oip': 0.0, 'secondary_agep': 0,
        }
        # PUMS replicate weights for SDR variance: weight_r01..weight_r80
        # (empty dict if include_replicate_weights=False or replicates absent).
        # n_members = len(person_weights): a one-person unit uses PWGTPr; a
        # single/HoH filer WITH dependents has >= 2 members and uses WGTPr,
        # matching the main weight's member-count rule.
        tax_unit.update(self._compute_replicate_weights(
            hh_data, adult, filing_status, len(person_weights),
        ))

        logger.debug(f"Created tax unit: {tax_unit}")
        return tax_unit
