"""
Tests for the income module.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from tax_modeler.units.income import calculate_person_income, calculate_tax_unit_income

# Test data
TEST_PERSON = pd.Series({
    'WAGP': 50000,     # Wages
    'SEMP': 10000,     # Self-employment income
    'INTP': 500,       # Interest income
    'RETP': 1000,      # Retirement income
    'SSP': 12000,      # Social Security benefits (85% taxable)
    'OIP': 500,        # Other income
    'ADJINC': 1000000  # Adjustment factor (1.0)
})

TEST_TAX_UNIT = pd.DataFrame([
    {'WAGP': 50000, 'SEMP': 10000, 'INTP': 500, 'RETP': 1000, 'SSP': 12000, 'OIP': 500, 'ADJINC': 1000000},
    {'WAGP': 30000, 'SEMP': 0, 'INTP': 200, 'RETP': 500, 'SSP': 0, 'OIP': 0, 'ADJINC': 1000000}
])

class TestIncomeCalculations:
    """Test cases for income calculations.

    NOTE: ``calculate_person_income`` defaults to ``apply_2026_growth=True``
    which projects 2023 PUMS income forward to 2026. These unit tests pin
    the *un-projected* raw-income arithmetic, so each call passes
    ``apply_2026_growth=False``.
    """

    def test_calculate_person_income(self):
        """Test calculation of person income."""
        # Regular income components
        expected = 50000 + 10000 + 500 + 1000 + (12000 * 0.85) + 500
        result = calculate_person_income(TEST_PERSON, apply_2026_growth=False)
        assert np.isclose(result, expected)

    def test_calculate_tax_unit_income(self):
        """Test calculation of tax unit income."""
        # Calculate expected as sum of individual incomes
        person1 = 50000 + 10000 + 500 + 1000 + (12000 * 0.85) + 500
        person2 = 30000 + 200 + 500
        expected = person1 + person2

        result = calculate_tax_unit_income(TEST_TAX_UNIT, apply_2026_growth=False)
        assert np.isclose(result, expected)

    def test_income_with_adjustment(self):
        """Test income calculation with ADJINC adjustment."""
        person = TEST_PERSON.copy()
        person['ADJINC'] = 1100000  # 1.1 adjustment

        base_income = 50000 + 10000 + 500 + 1000 + (12000 * 0.85) + 500
        expected = base_income * 1.1

        result = calculate_person_income(person, apply_2026_growth=False)
        assert np.isclose(result, expected)
