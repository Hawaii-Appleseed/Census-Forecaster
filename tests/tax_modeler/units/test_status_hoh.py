"""
Tests for the Head of Household status determination.
"""

from __future__ import annotations

import pandas as pd
from tax_modeler.units.status.hoh import (
    _has_qualifying_person,
    _is_unmarried,
    _paid_half_home_cost,
    is_head_of_household,
)


# Test data — RELSHIPP uses canonical Census 2019+ PUMS integer codes:
#   20 = Reference person, 21 = Spouse, 25 = Biological child,
#   29 = Parent, 35 = Foster child, 36 = Other nonrelative.
def create_test_household():
    """Create a test household with various members."""
    # Create household members
    members = [
        # Potential HOH - unmarried, has qualifying child
        {'SERIALNO': '1', 'SPORDER': '1', 'AGEP': 35, 'SEX': 1, 'MAR': 5, 'RELSHIPP': 20, 'HINCP': 40000, 'PAP': 0},
        # Qualifying child (biological child of householder)
        {'SERIALNO': '1', 'SPORDER': '2', 'AGEP': 10, 'SEX': 1, 'MAR': 0, 'RELSHIPP': 25, 'HINCP': 0, 'PAP': 0},
        # Non-qualifying person (unrelated adult — other nonrelative)
        {'SERIALNO': '1', 'SPORDER': '3', 'AGEP': 25, 'SEX': 2, 'MAR': 0, 'RELSHIPP': 36, 'HINCP': 20000, 'PAP': 0},
        # Qualifying relative (elderly parent)
        {'SERIALNO': '1', 'SPORDER': '4', 'AGEP': 70, 'SEX': 2, 'MAR': 3, 'RELSHIPP': 29, 'HINCP': 5000, 'PAP': 0},
    ]
    
    # Create DataFrame and set index
    df = pd.DataFrame(members)
    df['person_id'] = df['SERIALNO'] + '_' + df['SPORDER'].astype(str)
    df.set_index('person_id', inplace=True)
    
    return df

class TestHeadOfHousehold:
    """Test cases for Head of Household status determination."""
    
    def test_is_unmarried_positive(self):
        """Test that unmarried individuals are correctly identified."""
        hh_df = create_test_household()
        potential_hoh = hh_df.loc['1_1']  # MAR=5 (Never married)
        
        assert _is_unmarried(potential_hoh, hh_df) is True
    
    def test_is_unmarried_negative(self):
        """Test that married individuals living with their spouse are not
        identified as unmarried.

        Note: ``_is_unmarried`` returns True for married persons whose
        spouse isn't present in the household ("considered unmarried" for
        HoH purposes), so this test must include a spouse (RELSHIPP=21) to
        get the strict-married outcome.
        """
        members = [
            {'SERIALNO': '4', 'SPORDER': '1', 'AGEP': 35, 'SEX': 1, 'MAR': 1,
             'RELSHIPP': 20, 'HINCP': 50000, 'PAP': 0},
            {'SERIALNO': '4', 'SPORDER': '2', 'AGEP': 33, 'SEX': 2, 'MAR': 1,
             'RELSHIPP': 21, 'HINCP': 30000, 'PAP': 0},
        ]
        df = pd.DataFrame(members)
        df['person_id'] = df['SERIALNO'] + '_' + df['SPORDER'].astype(str)
        df.set_index('person_id', inplace=True)

        married_person = df.loc['4_1']
        assert _is_unmarried(married_person, df) is False
    
    def test_has_qualifying_person_positive_child(self):
        """Test identification of qualifying child."""
        hh_df = create_test_household()
        potential_hoh = hh_df.loc['1_1']  # Has a qualifying child (1_2)
        
        assert _has_qualifying_person(potential_hoh, hh_df) is True
    
    def test_has_qualifying_person_positive_relative(self):
        """Test identification of qualifying relative."""
        hh_df = create_test_household()
        # Create a person with a qualifying relative (elderly parent)
        person = hh_df.loc['1_3'].copy()  # Adult non-relative
        person['RELSHIPP'] = '01'  # Make them related to the elderly parent
        
        assert _has_qualifying_person(person, hh_df) is True
    
    def test_has_qualifying_person_negative(self):
        """Test case with no qualifying persons (single-adult household)."""
        # Build a household with NO children, parents, or other qualifying
        # relatives — just two unrelated adults.
        members = [
            {'SERIALNO': '2', 'SPORDER': '1', 'AGEP': 35, 'SEX': 1, 'MAR': 5,
             'RELSHIPP': 20, 'HINCP': 40000, 'PAP': 0},
            {'SERIALNO': '2', 'SPORDER': '2', 'AGEP': 28, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 36, 'HINCP': 30000, 'PAP': 0},  # Roommate
        ]
        df = pd.DataFrame(members)
        df['person_id'] = df['SERIALNO'] + '_' + df['SPORDER'].astype(str)
        df.set_index('person_id', inplace=True)

        person = df.loc['2_1']
        assert _has_qualifying_person(person, df) is False
    
    def test_paid_half_home_cost_positive(self):
        """Test that person paying >50% of home costs is identified."""
        hh_df = create_test_household()
        potential_hoh = hh_df.loc['1_1']  # Makes $40k out of $65k total
        
        assert _paid_half_home_cost(potential_hoh, hh_df) is True
    
    def test_paid_half_home_cost_negative(self):
        """Person who isn't householder, has no dependents, and contributes
        a tiny share of household income shouldn't pass the test."""
        # Two-adult, no-children household. WAGP set explicitly so the
        # function can compute contribution ratios.
        members = [
            {'SERIALNO': '3', 'SPORDER': '1', 'AGEP': 35, 'SEX': 1, 'MAR': 1,
             'RELSHIPP': 20, 'WAGP': 90000, 'PINCP': 90000, 'PAP': 0, 'ADJINC': 1000000},
            {'SERIALNO': '3', 'SPORDER': '2', 'AGEP': 28, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 36, 'WAGP': 5000, 'PINCP': 5000, 'PAP': 0, 'ADJINC': 1000000},
        ]
        df = pd.DataFrame(members)
        df['person_id'] = df['SERIALNO'] + '_' + df['SPORDER'].astype(str)
        df.set_index('person_id', inplace=True)

        person = df.loc['3_2']  # Roommate making $5k of $95k = ~5%
        assert _paid_half_home_cost(person, df) is False
    
    def test_is_head_of_household_positive(self):
        """Test a valid Head of Household scenario.

        Note: ``is_head_of_household`` requires ``has_dependents=True``
        because HoH status requires actually claiming a qualifying person
        as a dependent (not just having one in the household).
        """
        hh_df = create_test_household()
        potential_hoh = hh_df.loc['1_1']  # Meets all criteria

        assert is_head_of_household(potential_hoh, hh_df, has_dependents=True) is True

    def test_is_head_of_household_negative_married(self):
        """Test that married individuals cannot be HOH."""
        hh_df = create_test_household()
        married_person = hh_df.loc['1_1'].copy()
        married_person['MAR'] = 1  # Married
        # Add a spouse so _is_unmarried correctly identifies them as married.
        married_person_spouse = pd.Series({
            'SERIALNO': '1', 'SPORDER': '5', 'AGEP': 33, 'SEX': 2, 'MAR': 1,
            'RELSHIPP': 21, 'HINCP': 30000, 'PAP': 0,
        }, name='1_5')
        hh_with_spouse = pd.concat([hh_df, pd.DataFrame([married_person_spouse])])

        assert is_head_of_household(married_person, hh_with_spouse, has_dependents=True) is False

    def test_is_head_of_household_negative_no_qualifying_person(self):
        """Test that person with no qualifying dependents cannot be HOH."""
        hh_df = create_test_household()
        person = hh_df.loc['1_3']  # The roommate — not the HoH candidate

        # has_dependents=False because this person isn't claiming any deps.
        assert is_head_of_household(person, hh_df, has_dependents=False) is False
