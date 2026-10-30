"""
Tax Unit Validation Module

This package provides validation functionality for tax unit classification,
including machine learning-based validation of filing statuses.
"""

from __future__ import annotations

# Import base validation components first
from .base_validator import TaxUnitValidator, ValidationIssue, ValidationSeverity

# Import ML validator components
try:
    from .ml_validator import MLTaxUnitValidator, validate_tax_units
    ML_VALIDATOR_AVAILABLE = True
except ImportError:
    ML_VALIDATOR_AVAILABLE = False
    MLTaxUnitValidator = None
    validate_tax_units = None

__all__ = [
    'TaxUnitValidator',
    'ValidationIssue',
    'ValidationSeverity',
    'MLTaxUnitValidator', 
    'validate_tax_units',
    'ML_VALIDATOR_AVAILABLE'
]
