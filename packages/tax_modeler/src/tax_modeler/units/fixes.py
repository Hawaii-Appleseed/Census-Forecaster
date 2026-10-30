"""
Comprehensive fixes for tax unit overcounting issues.

This module provides fixes for the identified overcounting problems:
1. Standardize filing status values
2. Ensure proper household-to-tax-unit mapping
3. Handle households without persons
4. Fix dependent field handling
5. Add deduplication logic
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

class TaxUnitOvercountingFix:
    """Fix overcounting issues in tax unit construction."""
    
    def __init__(self):
        self.processed_households = set()
        self.filing_status_mapping = {
            'joint': 'married_filing_jointly',
            'married_filing_jointly': 'married_filing_jointly',
            'married_filing_separately': 'married_filing_separately',
            'married_filing_separate': 'married_filing_separately',  # Fix common variant
            'head_of_household': 'head_of_household',
            'single': 'single'
        }
    
    def filter_valid_households(self, person_df: pd.DataFrame, hh_df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Filter out households without persons to prevent empty household processing."""
        logger.info("Filtering valid households...")
        
        # Get households that have person records
        person_households = set(person_df['SERIALNO'].unique())
        hh_households = set(hh_df['SERIALNO'].unique())
        
        # Find households without persons
        households_without_persons = hh_households - person_households
        households_without_hh_data = person_households - hh_households
        
        logger.info(f"Households without persons: {len(households_without_persons):,}")
        logger.info(f"Persons without household data: {len(households_without_hh_data):,}")
        
        # Filter household data to only include households with persons
        valid_hh_df = hh_df[hh_df['SERIALNO'].isin(person_households)].copy()
        
        # Filter person data to only include persons with household data
        valid_person_df = person_df[person_df['SERIALNO'].isin(hh_households)].copy()
        
        logger.info(f"Filtered to {len(valid_hh_df):,} households and {len(valid_person_df):,} persons")
        
        return valid_person_df, valid_hh_df
    
    def standardize_filing_status(self, tax_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Standardize filing status values across all tax units."""
        logger.info("Standardizing filing status values...")
        
        standardized_units = []
        for unit in tax_units:
            unit_copy = unit.copy()
            current_status = unit_copy.get('filing_status', 'single')
            standardized_status = self.filing_status_mapping.get(current_status, current_status)
            unit_copy['filing_status'] = standardized_status
            standardized_units.append(unit_copy)
        
        # Log filing status distribution
        status_counts = {}
        for unit in standardized_units:
            status = unit['filing_status']
            status_counts[status] = status_counts.get(status, 0) + 1
        
        logger.info("Standardized filing status distribution:")
        for status, count in status_counts.items():
            logger.info(f"  {status}: {count:,}")
        
        return standardized_units
    
    def deduplicate_tax_units(self, tax_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Remove duplicate tax units based on filer IDs and household IDs."""
        logger.info("Deduplicating tax units...")
        
        seen_units = set()
        deduplicated_units = []
        
        for unit in tax_units:
            # Create a unique key for this tax unit
            primary_filer = unit.get('primary_filer_id', '')
            secondary_filer = unit.get('secondary_filer_id', '')
            hh_id = unit.get('hh_id', '')
            filing_status = unit.get('filing_status', '')
            
            # Sort filer IDs to ensure consistent key regardless of order
            filer_ids = sorted([f for f in [primary_filer, secondary_filer] if f])
            unit_key = (hh_id, filing_status, tuple(filer_ids))
            
            if unit_key not in seen_units:
                seen_units.add(unit_key)
                deduplicated_units.append(unit)
            else:
                logger.debug(f"Removing duplicate tax unit: {unit_key}")
        
        removed_count = len(tax_units) - len(deduplicated_units)
        if removed_count > 0:
            logger.info(f"Removed {removed_count:,} duplicate tax units")
        
        return deduplicated_units
    
    def validate_household_tax_unit_mapping(self, tax_units: list[dict[str, Any]], 
                                          expected_households: int) -> dict[str, Any]:
        """Validate that the household-to-tax-unit mapping is reasonable."""
        logger.info("Validating household-to-tax-unit mapping...")
        
        # Group tax units by household
        household_units = {}
        for unit in tax_units:
            hh_id = unit.get('hh_id', '')
            if hh_id not in household_units:
                household_units[hh_id] = []
            household_units[hh_id].append(unit)
        
        # Analyze distribution
        units_per_household = [len(units) for units in household_units.values()]
        
        validation_results = {
            'total_tax_units': len(tax_units),
            'total_households_with_units': len(household_units),
            'expected_households': expected_households,
            'avg_units_per_household': np.mean(units_per_household),
            'max_units_per_household': max(units_per_household) if units_per_household else 0,
            'households_with_multiple_units': sum(1 for count in units_per_household if count > 1),
            'distribution': {
                '1_unit': sum(1 for count in units_per_household if count == 1),
                '2_units': sum(1 for count in units_per_household if count == 2),
                '3_plus_units': sum(1 for count in units_per_household if count >= 3)
            }
        }
        
        logger.info("Tax unit validation results:")
        logger.info(f"  Total tax units: {validation_results['total_tax_units']:,}")
        logger.info(f"  Households with units: {validation_results['total_households_with_units']:,}")
        logger.info(f"  Expected households: {validation_results['expected_households']:,}")
        logger.info(f"  Avg units per household: {validation_results['avg_units_per_household']:.2f}")
        logger.info(f"  Max units per household: {validation_results['max_units_per_household']}")
        logger.info(f"  Households with multiple units: {validation_results['households_with_multiple_units']:,}")
        
        # Flag potential issues
        issues = []
        if validation_results['avg_units_per_household'] > 1.5:
            issues.append("High average tax units per household (>1.5)")
        
        if validation_results['max_units_per_household'] > 3:
            issues.append(f"Very high max units per household ({validation_results['max_units_per_household']})")
        
        if validation_results['households_with_multiple_units'] > expected_households * 0.3:
            issues.append("Too many households with multiple tax units (>30%)")
        
        if issues:
            logger.warning("Potential overcounting issues detected:")
            for issue in issues:
                logger.warning(f"  - {issue}")
        
        validation_results['issues'] = issues
        return validation_results
    
    def fix_dependent_field_handling(self, tax_units: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Fix dependent field handling to prevent array ambiguity errors."""
        logger.info("Fixing dependent field handling...")
        
        fixed_units = []
        for unit in tax_units:
            unit_copy = unit.copy()
            dependents = unit_copy.get('dependents', [])
            
            # Ensure dependents is always a list
            if dependents is None:
                unit_copy['dependents'] = []
            elif isinstance(dependents, str):
                unit_copy['dependents'] = [dependents] if dependents else []
            elif isinstance(dependents, (tuple, set)):
                unit_copy['dependents'] = list(dependents)
            elif not isinstance(dependents, list):
                # Handle numpy arrays or other iterables
                try:
                    unit_copy['dependents'] = list(dependents)
                except (TypeError, ValueError):
                    unit_copy['dependents'] = []
            
            # Ensure num_dependents matches the actual count
            unit_copy['num_dependents'] = len(unit_copy['dependents'])
            
            fixed_units.append(unit_copy)
        
        return fixed_units
    
    def apply_comprehensive_fixes(self, person_df: pd.DataFrame, hh_df: pd.DataFrame,
                                tax_units: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
        """Apply all comprehensive fixes for overcounting issues."""
        logger.info("Applying comprehensive overcounting fixes...")
        
        # Step 1: Filter valid households
        valid_person_df, valid_hh_df = self.filter_valid_households(person_df, hh_df)
        
        # Step 2: Standardize filing status
        standardized_units = self.standardize_filing_status(tax_units)
        
        # Step 3: Fix dependent field handling
        fixed_units = self.fix_dependent_field_handling(standardized_units)
        
        # Step 4: Deduplicate tax units
        deduplicated_units = self.deduplicate_tax_units(fixed_units)
        
        # Step 5: Validate household-to-tax-unit mapping
        validation_results = self.validate_household_tax_unit_mapping(
            deduplicated_units, len(valid_hh_df)
        )
        
        logger.info("Comprehensive fixes applied successfully!")
        
        return valid_person_df, valid_hh_df, deduplicated_units, validation_results

def apply_overcounting_fixes(person_df: pd.DataFrame, hh_df: pd.DataFrame,
                           tax_units: list[dict[str, Any]]) -> tuple[pd.DataFrame, pd.DataFrame, list[dict[str, Any]], dict[str, Any]]:
    """Convenience function to apply all overcounting fixes."""
    fixer = TaxUnitOvercountingFix()
    return fixer.apply_comprehensive_fixes(person_df, hh_df, tax_units)
