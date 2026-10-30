"""
Tests for the tax unit validation module.
"""

from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.units.validation import TaxUnitValidator, ValidationIssue, ValidationSeverity


def test_validate_tax_unit_complete():
    """Test validation of a complete and valid tax unit."""
    tax_unit = {
        'filer_id': '123',
        'filing_status': 'single',
        'income': 50000,
        'num_dependents': 1,
        'dependents': ['dep1'],
        'hh_id': 'hh1',
        'primary_filer_id': '123',
        'secondary_filer_id': None
    }
    
    issues = TaxUnitValidator.validate_tax_unit(tax_unit)
    assert len(issues) == 0


def test_validate_tax_unit_missing_field():
    """Test validation detects missing required fields."""
    tax_unit = {
        'filer_id': '123',
        'filing_status': 'single',
        # Missing 'income' field
        'num_dependents': 0,
        'dependents': [],
        'hh_id': 'hh1'
    }
    
    issues = TaxUnitValidator.validate_tax_unit(tax_unit)
    assert any(issue.field == 'income' for issue in issues)


def test_validate_tax_unit_invalid_status():
    """Test validation of invalid filing status."""
    tax_unit = {
        'filer_id': '123',
        'filing_status': 'invalid_status',  # Invalid status
        'income': 50000,
        'num_dependents': 0,
        'dependents': [],
        'hh_id': 'hh1'
    }
    
    issues = TaxUnitValidator.validate_tax_unit(tax_unit)
    assert any(issue.field == 'filing_status' for issue in issues)


def test_validate_tax_unit_dependent_mismatch():
    """Test validation of dependent count mismatch."""
    tax_unit = {
        'filer_id': '123',
        'filing_status': 'single',
        'income': 50000,
        'num_dependents': 2,  # Mismatch with dependents list
        'dependents': ['dep1'],
        'hh_id': 'hh1'
    }
    
    issues = TaxUnitValidator.validate_tax_unit(tax_unit)
    assert any(issue.field == 'num_dependents' for issue in issues)


def test_validate_tax_units_duplicate_ids():
    """Test validation detects duplicate filer IDs."""
    tax_units = [
        {
            'filer_id': '123',
            'filing_status': 'single',
            'income': 50000,
            'num_dependents': 0,
            'dependents': [],
            'hh_id': 'hh1'
        },
        {
            'filer_id': '123',  # Duplicate ID
            'filing_status': 'single',
            'income': 60000,
            'num_dependents': 0,
            'dependents': [],
            'hh_id': 'hh1'
        }
    ]
    
    issues = TaxUnitValidator.validate_tax_units(tax_units)
    assert any('duplicate' in issue.message.lower() for issue in issues)


def test_validate_household_coverage():
    """Test validation of household member coverage."""
    tax_units = [
        {
            'filer_id': '123',
            'filing_status': 'single',
            'income': 50000,
            'num_dependents': 0,
            'dependents': [],
            'hh_id': 'hh1',
            'primary_filer_id': '123',
            'secondary_filer_id': None
        }
    ]
    
    # Household with two members, but only one is assigned
    household_members = pd.DataFrame({
        'SERIALNO': ['hh1', 'hh1'],
        'SPORDER': ['1', '2'],
        'AGEP': [30, 25],
        'RELSHIPP': [20, 25]  # 20=Householder, 25=Biological child
    }).set_index('SPORDER')
    
    issues = TaxUnitValidator.validate_household_coverage(tax_units, household_members)
    assert any('unassigned' in issue.message.lower() for issue in issues)


def test_validation_severity_levels():
    """Test validation severity levels."""
    # Test error severity
    error_issue = ValidationIssue(
        "Test error",
        ValidationSeverity.ERROR,
        tax_unit_id='123',
        field='test_field'
    )
    assert error_issue.severity == ValidationSeverity.ERROR
    
    # Test warning severity
    warning_issue = ValidationIssue(
        "Test warning",
        ValidationSeverity.WARNING,
        tax_unit_id='123',
        field='test_field'
    )
    assert warning_issue.severity == ValidationSeverity.WARNING


def test_validation_issue_representation():
    """Test string representation of validation issues."""
    issue = ValidationIssue(
        "Test message",
        ValidationSeverity.ERROR,
        tax_unit_id='123',
        field='test_field',
        details={'key': 'value'}
    )
    
    # Convert to string and check for expected components
    issue_str = str(issue)
    assert "Test message" in issue_str
    assert "ERROR" in issue_str
    assert "test_field" in issue_str
    assert "123" in issue_str
    assert "ValidationIssue" in issue_str


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
