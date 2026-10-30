"""
Tax unit construction and analysis module.

This package provides functionality for constructing tax units from PUMS data
and analyzing their characteristics for tax policy modeling.
"""

from __future__ import annotations

"""
Tax unit construction and analysis module.

This package provides functionality for constructing tax units from PUMS data
and analyzing their characteristics for tax policy modeling.
"""

# Import key classes and functions to make them available at package level
from .base import FILING_STATUS, TaxUnitConstructor
from .dependencies import identify_dependents
from .income import calculate_tax_unit_income
from .status import is_head_of_household, is_married_filing_jointly, is_married_filing_separately

# Import validation components directly to avoid circular imports
MLTaxUnitValidator = None
try:
    from .validation.ml_validator import MLTaxUnitValidator, validate_tax_units
except ImportError:
    pass

# Define what gets imported with 'from tax_modeler.units import *'
__all__ = [
    'TaxUnitConstructor',
    'MLTaxUnitValidator',
    'validate_tax_units',
    'identify_dependents',
    'calculate_tax_unit_income',
    'is_head_of_household',
    'is_married_filing_jointly',
    'is_married_filing_separately',
    'FILING_STATUS'
]

# Version information
__version__ = '0.1.0'
