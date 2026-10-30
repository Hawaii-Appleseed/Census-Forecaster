"""
PUMA 0100 County Imputation Module

This module provides methods to separate Maui and Kauai counties from the combined
PUMA 0100 using population-based allocation enhanced with demographic factors.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from functools import cache

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@cache
def _seed_of(seed_string: str) -> int:
    """The county draw's seed for one unit: MD5 of its seed string. Cached,
    since every projection imputes the same units again."""
    return int(hashlib.md5(seed_string.encode()).hexdigest()[:8], 16)


class PUMA0100Imputer:
    """
    Imputes county assignments for PUMA 0100 tax units using population-based
    allocation enhanced with demographic factors.
    """
    
    def __init__(self):
        # 2023 Census population data
        self.base_allocation = {
            'Maui': 0.689,  # 163,688 / 237,528
            'Kauai': 0.311  # 73,840 / 237,528
        }
        
        # Demographic adjustment factors based on Hawaii economic patterns
        self.demographic_factors = {
            'income_thresholds': {
                'low': 50000,      # Below this favors Kauai (more rural/agricultural)
                'high': 100000     # Above this favors Maui (more tourism/service)
            },
            'family_size_factors': {
                'small': 2,        # 1-2 people
                'large': 4         # 4+ people
            },
            'age_factors': {
                'young_adult': (25, 35),  # Tourism workers
                'retirement': (65, 100)   # Retirees
            },
            'housing_type': {
                'detached': {'Maui': 0.554, 'Kauai': 0.85},  # % of housing that's detached
                'multifamily': {'Maui': 0.297, 'Kauai': 0.05},  # 2-4 units + 10+ units
                'adjustment_strength': 0.1  # How strongly to weight housing type
            },
            'property_value': {
                'median': {
                    'Maui': 858600,
                    'Kauai': 817900,
                    'difference': 40700  # $40,700 higher in Maui
                },
                'adjustment_strength': 0.05,  # Moderate strength for value adjustment
                'value_ratio': 1.05  # Maui is ~1.05x more expensive than Kauai
            }
        }
    
    def calculate_county_probability(self, tax_unit: dict) -> dict[str, float]:
        """
        Calculate probability of assignment to Maui vs Kauai based on demographics.
        
        Args:
            tax_unit: Dictionary containing tax unit data
            
        Returns:
            Dictionary with 'Maui' and 'Kauai' probabilities
        """
        # Start with base population allocation
        maui_prob = self.base_allocation['Maui']
        kauai_prob = self.base_allocation['Kauai']
        
        # Income-based adjustment
        income = tax_unit.get('income', 0)
        if income < self.demographic_factors['income_thresholds']['low']:
            # Lower income slightly favors Kauai (more agricultural/rural)
            adjustment = 0.05
            maui_prob -= adjustment
            kauai_prob += adjustment
        elif income > self.demographic_factors['income_thresholds']['high']:
            # Higher income slightly favors Maui (more tourism/service economy)
            adjustment = 0.08
            maui_prob += adjustment
            kauai_prob -= adjustment
        
        # Family size adjustment
        num_dependents = tax_unit.get('num_dependents', 0)
        filing_status = tax_unit.get('filing_status', 'single')
        
        # Estimate household size
        household_size = 1
        if filing_status == 'joint':
            household_size = 2
        household_size += num_dependents
        
        if household_size >= self.demographic_factors['family_size_factors']['large']:
            # Larger families slightly favor Kauai (more space, lower cost)
            adjustment = 0.03
            maui_prob -= adjustment
            kauai_prob += adjustment
        elif household_size <= self.demographic_factors['family_size_factors']['small']:
            # Smaller households slightly favor Maui (urban amenities)
            adjustment = 0.02
            maui_prob += adjustment
            kauai_prob -= adjustment
        
        # Housing type adjustment if available
        if 'housing_type' in tax_unit and tax_unit['housing_type']:
            housing_type = str(tax_unit['housing_type']).lower()
            if 'detach' in housing_type or 'single' in housing_type:
                # Detached housing is more common in Kauai
                adjustment = self.demographic_factors['housing_type']['adjustment_strength']
                maui_prob -= adjustment * 0.5  # Slight adjustment since both counties have significant detached housing
                kauai_prob += adjustment * 0.5
            elif 'multi' in housing_type or 'apartment' in housing_type or 'condo' in housing_type:
                # Multi-family housing is much more common in Maui
                adjustment = self.demographic_factors['housing_type']['adjustment_strength'] * 1.5
                maui_prob += adjustment
                kauai_prob -= adjustment
        
        # Property value adjustment if available (VALP in PUMS)
        if 'property_value' in tax_unit and pd.notna(tax_unit['property_value']):
            try:
                prop_value = float(tax_unit['property_value'])
                if prop_value > 0:
                    # Calculate adjustment based on how much the value differs from the median
                    maui_ratio = prop_value / self.demographic_factors['property_value']['median']['Maui']
                    kauai_ratio = prop_value / self.demographic_factors['property_value']['median']['Kauai']
                    
                    # If value is closer to Maui's median, increase Maui probability
                    if abs(maui_ratio - 1) < abs(kauai_ratio - 1):
                        adjustment = self.demographic_factors['property_value']['adjustment_strength'] * (1.1 - maui_ratio)
                    else:
                        adjustment = -self.demographic_factors['property_value']['adjustment_strength'] * (1.1 - kauai_ratio)
                    
                    # Apply the adjustment
                    maui_prob += adjustment
                    kauai_prob -= adjustment
            except (ValueError, TypeError):
                # Skip if property value can't be converted to float
                pass
        
        # Ensure probabilities remain within reasonable bounds
        maui_prob = max(0.1, min(0.9, maui_prob))
        kauai_prob = max(0.1, min(0.9, kauai_prob))
        
        # Ensure probabilities sum to 1.0
        total = maui_prob + kauai_prob
        maui_prob /= total
        kauai_prob /= total
        
        return {'Maui': maui_prob, 'Kauai': kauai_prob}
    
    @staticmethod
    def _unit_seed(tax_unit: Mapping) -> int:
        tax_unit_id = tax_unit.get('filer_id', tax_unit.get('SERIALNO', ''))
        return _seed_of(f"puma0100_{tax_unit_id}_county_assignment")

    def assign_county(self, tax_unit: dict, use_deterministic: bool = True) -> str:
        """
        Assign a county to a tax unit from PUMA 0100.
        
        Args:
            tax_unit: Dictionary containing tax unit data
            use_deterministic: If True, uses deterministic assignment based on tax unit ID
            
        Returns:
            County name ('Maui' or 'Kauai')
        """
        probabilities = self.calculate_county_probability(tax_unit)
        
        if use_deterministic:
            # Use deterministic randomization based on tax unit ID for consistency
            np.random.seed(self._unit_seed(tax_unit))
        
        # Assign based on probabilities
        random_value = np.random.random()
        if random_value < probabilities['Maui']:
            return 'Maui'
        else:
            return 'Kauai'
    
    def impute_counties(self, tax_units_df: pd.DataFrame) -> pd.DataFrame:
        """
        Impute county assignments for all PUMA 0100 tax units.
        
        Args:
            tax_units_df: DataFrame containing tax units with PUMA information
            
        Returns:
            DataFrame with imputed county assignments
        """
        df = tax_units_df.copy()
        
        # Identify PUMA 0100 tax units
        puma_0100_mask = df['PUMA'].astype(str).isin(['100', '0100'])
        puma_0100_count = puma_0100_mask.sum()
        
        if puma_0100_count == 0:
            logger.info("No PUMA 0100 tax units found for imputation")
            return df
        
        logger.info(f"Imputing county assignments for {puma_0100_count:,} PUMA 0100 tax units")
        
        # Apply imputation to PUMA 0100 tax units: assign_county for each,
        # reading only the columns it uses (per_unit.unit_rows) and drawing
        # from a generator seeded the way assign_county seeds the global one.
        from tax_modeler.per_unit import unit_rows

        imputed_counties = []
        seed = None
        rng = np.random.RandomState()
        for tax_unit in unit_rows(df[puma_0100_mask]):
            probabilities = self.calculate_county_probability(tax_unit)
            seed = self._unit_seed(tax_unit)
            rng.seed(seed)
            draw = rng.random_sample()
            imputed_counties.append('Maui' if draw < probabilities['Maui'] else 'Kauai')
        if seed is not None:
            # Leave numpy's global generator as assign_county's reseeding did:
            # later draws in the same process depend on it.
            np.random.seed(seed)
            np.random.random()
        
        # Update the county column for PUMA 0100 tax units
        df.loc[puma_0100_mask, 'county'] = imputed_counties
        
        # Log results
        imputation_results = pd.Series(imputed_counties).value_counts()
        logger.info("Imputation results:")
        for county, count in imputation_results.items():
            percentage = (count / puma_0100_count) * 100
            logger.info(f"  {county}: {count:,} tax units ({percentage:.1f}%)")
        
        # Validate against expected population ratios
        maui_actual = (pd.Series(imputed_counties) == 'Maui').mean()
        kauai_actual = (pd.Series(imputed_counties) == 'Kauai').mean()
        
        logger.info("Validation against 2023 Census population ratios:")
        logger.info(f"  Maui - Expected: {self.base_allocation['Maui']:.1%}, Actual: {maui_actual:.1%}")
        logger.info(f"  Kauai - Expected: {self.base_allocation['Kauai']:.1%}, Actual: {kauai_actual:.1%}")
        
        return df
    
    def get_imputation_summary(self, tax_units_df: pd.DataFrame) -> dict:
        """
        Generate summary statistics for the imputation process.
        
        Args:
            tax_units_df: DataFrame with imputed counties
            
        Returns:
            Dictionary containing summary statistics
        """
        puma_0100_mask = tax_units_df['PUMA'].astype(str).isin(['100', '0100'])
        puma_0100_units = tax_units_df[puma_0100_mask]
        
        if len(puma_0100_units) == 0:
            return {"error": "No PUMA 0100 tax units found"}
        
        summary = {
            'total_puma_0100_units': len(puma_0100_units),
            'county_distribution': puma_0100_units['county'].value_counts().to_dict(),
            'avg_income_by_county': puma_0100_units.groupby('county')['income'].mean().to_dict(),
            'avg_dependents_by_county': puma_0100_units.groupby('county')['num_dependents'].mean().to_dict(),
            'filing_status_by_county': puma_0100_units.groupby('county')['filing_status'].value_counts().to_dict()
        }
        
        return summary


def impute_puma_0100_counties(tax_units_df: pd.DataFrame, 
                             validate_results: bool = True) -> pd.DataFrame:
    """
    Convenience function to impute counties for PUMA 0100 tax units.
    
    Args:
        tax_units_df: DataFrame containing tax units
        validate_results: Whether to validate results against expected ratios
        
    Returns:
        DataFrame with imputed county assignments
    """
    imputer = PUMA0100Imputer()
    result_df = imputer.impute_counties(tax_units_df)
    
    if validate_results:
        summary = imputer.get_imputation_summary(result_df)
        logger.info("Imputation Summary:")
        logger.info(f"Total PUMA 0100 units processed: {summary.get('total_puma_0100_units', 0):,}")
        
        county_dist = summary.get('county_distribution', {})
        for county, count in county_dist.items():
            logger.info(f"{county}: {count:,} tax units")
    
    return result_df
