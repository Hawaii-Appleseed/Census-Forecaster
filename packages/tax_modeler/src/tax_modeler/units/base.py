"""
Base module for tax unit construction.

Contains the core TaxUnitConstructor class and base functionality.
"""

from __future__ import annotations

import logging

import pandas as pd

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Filing status constants — values match the strings emitted in the
# ``filing_status`` column of the DataFrame returned by
# ``TaxUnitConstructor.create_rule_based_units``. The shorter forms
# (``'joint'``, ``'separate'``) are also produced in a sibling
# ``filing_status_normalized`` column for callers (e.g. IPF calibration)
# that prefer them.
FILING_STATUS = {
    'SINGLE': 'single',
    'JOINT': 'married_filing_jointly',
    'SEPARATE': 'married_filing_separately',
    'HEAD_OF_HOUSEHOLD': 'head_of_household',
    'QUALIFYING_WIDOW': 'qualifying_widow',
}

class TaxUnitConstructor:
    """
    Core class for constructing tax units from PUMS data.
    
    This class handles the main logic for identifying tax filing units,
    determining filing status, and calculating tax-related attributes.
    """
    
    def __init__(self, person_df: pd.DataFrame, hh_df: pd.DataFrame):
        """
        Initialize the TaxUnitConstructor with person and household data.
        
        Args:
            person_df: DataFrame containing person-level PUMS data
            hh_df: DataFrame containing household-level PUMS data
        """
        self.person_df = person_df.copy()
        self.hh_df = hh_df.copy()
        self._preprocess_data()
    
    def _preprocess_data(self) -> None:
        """Preprocess the input data for tax unit construction."""
        # Ensure SERIALNO and SPORDER are strings for consistent ID generation
        self.person_df['SERIALNO'] = self.person_df['SERIALNO'].astype(str)
        self.person_df['SPORDER'] = self.person_df['SPORDER'].astype(str)
        
        # Create a unique person ID
        self.person_df['person_id'] = (
            self.person_df['SERIALNO'] + '_' + self.person_df['SPORDER']
        )
        
        # Set person_id as the index
        self.person_df.set_index('person_id', inplace=True)
        
        # Pre-calculate common values
        self.person_df['is_adult'] = self.person_df['AGEP'] >= 18
        self.person_df['is_child'] = ~self.person_df['is_adult']
        
        # Merge household data
        self.person_df = self.person_df.merge(
            self.hh_df[['SERIALNO', 'HINCP']], 
            on='SERIALNO', 
            how='left'
        )
    
    def create_rule_based_units(self) -> pd.DataFrame:
        """
        Create tax units using rule-based approach.
        
        Returns:
            DataFrame containing the constructed tax units
        """
        logger.info("Creating tax units using rule-based approach...")
        
        # Placeholder for tax units
        tax_units = []
        
        # Process each household
        for _hh_id, hh_group in self.person_df.groupby('SERIALNO'):
            # Skip households with no adults
            if not hh_group['is_adult'].any():
                continue
                
            # Process household members into tax units
            household_units = self._process_household(hh_group)
            tax_units.extend(household_units)
        
        # Convert to DataFrame
        if tax_units:
            return pd.DataFrame(tax_units)
        return pd.DataFrame()
    
    def _process_household(self, hh_group: pd.DataFrame) -> list[dict]:
        """
        Process a single household into tax filing units.
        
        Args:
            hh_group: DataFrame containing all persons in a household
            
        Returns:
            List of tax unit dictionaries
        """
        # This will be implemented in the status-specific modules
        raise NotImplementedError(
            "Subclasses must implement _process_household"
        )
    
    def _calculate_income(self, person: pd.Series) -> float:
        """
        Calculate total income for a person.
        
        Args:
            person: Series containing person data
            
        Returns:
            Total income as float
        """
        # This will be implemented in the income module
        raise NotImplementedError(
            "Subclasses must implement _calculate_income"
        )
