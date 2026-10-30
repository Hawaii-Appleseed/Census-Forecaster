"""
Tests for the Married Filing Jointly status determination.
"""

from __future__ import annotations

import pandas as pd
from tax_modeler.units.status.mfj import (
    _are_citizens_or_residents,
    _are_married,
    _is_married_to_someone_else,
    is_married_filing_jointly,
)


# Test data
def create_test_person(serialno, sporder, age, married=True, relshipp=20, cit=1):
    """Helper function to create a test person."""
    # Create a unique ID for the person
    person_id = f"{serialno}_{sporder}"
    
    # Create the Series with the ID as the index
    person = pd.Series({
        'SERIALNO': str(serialno),
        'SPORDER': str(sporder),
        'AGEP': age,
        'MAR': 1 if married else 0,
        'RELSHIPP': relshipp,  # Changed to expect integer
        'CIT': cit,
        'is_dependent': False
    }, name=person_id)
    
    return person

class TestMarriedFilingJointly:
    """Test cases for Married Filing Jointly status determination."""
    
    def test_are_married_positive(self):
        """Test that a married couple is correctly identified."""
        # Create a married couple
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)  # Reference person (householder)
        person2 = create_test_person('1', '2', 38, married=True, relshipp=21)  # Spouse
        
        assert _are_married(person1, person2) is True
    
    def test_are_married_negative_not_married(self):
        """Test that non-married individuals are not identified as married."""
        person1 = create_test_person('1', '1', 40, married=False, relshipp=20)
        person2 = create_test_person('1', '2', 38, married=False, relshipp=21)
        
        assert _are_married(person1, person2) is False
    
    def test_are_married_negative_wrong_relationship(self):
        """Test that people with wrong relationship codes are not identified as married."""
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)
        person2 = create_test_person('1', '3', 15, married=False, relshipp=0)  # Child
        
        assert _are_married(person1, person2) is False
    
    def test_are_citizens_or_residents_positive(self):
        """Test that citizens and residents are correctly identified."""
        # Test all valid CIT codes (1-4)
        for cit in [1, 2, 3, 4]:
            person1 = create_test_person('1', '1', 40, cit=cit)
            person2 = create_test_person('1', '2', 38, cit=cit)
            assert _are_citizens_or_residents(person1, person2) is True
    
    def test_are_citizens_or_residents_negative(self):
        """Test that non-citizens/non-residents are correctly identified."""
        # CIT=5 means not a citizen
        person1 = create_test_person('1', '1', 40, cit=5)
        person2 = create_test_person('1', '2', 38, cit=1)
        assert _are_citizens_or_residents(person1, person2) is False
    
    def test_is_married_filing_jointly_positive(self):
        """Test a valid married filing jointly couple."""
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)
        person2 = create_test_person('1', '2', 38, married=True, relshipp=21)
        
        # Create a minimal person dataframe for testing
        person_df = pd.DataFrame([person1, person2])
        
        assert is_married_filing_jointly(person1, person2, person_df) is True
    
    def test_is_married_filing_jointly_negative_dependent(self):
        """Test that dependents cannot file jointly."""
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)
        person2 = create_test_person('1', '2', 38, married=True, relshipp=21)
        person2['is_dependent'] = True  # One is a dependent
        
        person_df = pd.DataFrame([person1, person2])
        
        assert is_married_filing_jointly(person1, person2, person_df) is False
    
    def test_is_married_filing_jointly_negative_not_married(self):
        """Test that non-married individuals cannot file jointly."""
        person1 = create_test_person('1', '1', 40, married=False, relshipp=20)
        person2 = create_test_person('1', '2', 38, married=False, relshipp=21)
        
        person_df = pd.DataFrame([person1, person2])
        
        assert is_married_filing_jointly(person1, person2, person_df) is False
    
    def test_is_married_filing_jointly_negative_not_citizens(self):
        """Test that non-citizens cannot file jointly."""
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20, cit=5)
        person2 = create_test_person('1', '2', 38, married=True, relshipp=21, cit=5)
        
        person_df = pd.DataFrame([person1, person2])
        
        assert is_married_filing_jointly(person1, person2, person_df) is False
        
    def test_is_married_to_someone_else_positive(self):
        """Test that we can detect when someone is married to someone else."""
        # Create a person who is married to someone else
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)
        spouse1 = create_test_person('1', '2', 38, married=True, relshipp=21)
        other_spouse = create_test_person('1', '3', 35, married=True, relshipp=21)  # Another spouse in same household
        
        person_df = pd.DataFrame([person1, spouse1, other_spouse])
        
        # person1 is married to spouse1, but there's another spouse in the household
        assert _is_married_to_someone_else(person1, spouse1, person_df) is True
        
    def test_is_married_to_someone_else_negative(self):
        """Test that we don't flag someone as married to someone else when they're not."""
        # Create a person who is only married to one person
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)
        spouse1 = create_test_person('1', '2', 38, married=True, relshipp=21)
        child = create_test_person('1', '3', 10, married=False, relshipp=0)  # Child, not a spouse
        
        person_df = pd.DataFrame([person1, spouse1, child])
        
        # person1 is only married to spouse1
        assert _is_married_to_someone_else(person1, spouse1, person_df) is False
        
    def test_is_married_to_someone_else_not_married(self):
        """Test that we handle non-married people correctly."""
        # Create a person who is not married
        person1 = create_test_person('1', '1', 40, married=False, relshipp=0)
        other_person = create_test_person('1', '2', 38, married=True, relshipp=21)
        
        person_df = pd.DataFrame([person1, other_person])
        
        # person1 is not married, so can't be married to someone else
        assert _is_married_to_someone_else(person1, other_person, person_df) is False
        
    def test_are_citizens_or_residents_edge_cases(self):
        """Test edge cases for citizen/resident status."""
        # Test with one citizen and one non-citizen
        person1 = create_test_person('1', '1', 40, cit=1)  # Citizen
        person2 = create_test_person('1', '2', 38, cit=5)  # Non-citizen
        assert _are_citizens_or_residents(person1, person2) is False
        
        # Test with one resident alien and one non-citizen
        person1 = create_test_person('1', '1', 40, cit=4)  # Resident alien
        person2 = create_test_person('1', '2', 38, cit=5)  # Non-citizen
        assert _are_citizens_or_residents(person1, person2) is False
        
    def test_married_to_someone_else_with_other_household_members(self):
        """Test the case where there are other household members but no other spouses."""
        # Create a household with a married couple and a child
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)  # Householder
        spouse = create_test_person('1', '2', 38, married=True, relshipp=21)   # Spouse
        child = create_test_person('1', '3', 10, married=False, relshipp=0)    # Child
        
        person_df = pd.DataFrame([person1, spouse, child])
        
        # person1 is only married to spouse, not to the child
        assert _is_married_to_someone_else(person1, spouse, person_df) is False
        
    def test_married_to_someone_else_empty_household(self):
        """Test the case where the household is empty except for the person and their spouse."""
        # Create a household with just the married couple
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)  # Householder
        spouse = create_test_person('1', '2', 38, married=True, relshipp=21)   # Spouse
        
        # Create a DataFrame with just these two people
        person_df = pd.DataFrame([person1, spouse])
        
        # person1 is only married to spouse, and there's no one else in the household
        assert _is_married_to_someone_else(person1, spouse, person_df) is False
        
    def test_is_married_filing_jointly_with_dependent(self):
        """Test that dependents cannot file jointly."""
        # Create a married couple where one is a dependent
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)
        person2 = create_test_person('1', '2', 38, married=True, relshipp=21)
        person2['is_dependent'] = True  # Make person2 a dependent
        
        person_df = pd.DataFrame([person1, person2])
        
        # Should return False because one is a dependent
        assert is_married_filing_jointly(person1, person2, person_df) is False
        
    def test_is_married_filing_jointly_married_to_someone_else(self):
        """Test that people married to someone else cannot file jointly."""
        # Create a household with a person married to someone else
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)  # Householder
        spouse1 = create_test_person('1', '2', 38, married=True, relshipp=21)  # First spouse
        spouse2 = create_test_person('1', '3', 35, married=True, relshipp=21)  # Second spouse
        
        person_df = pd.DataFrame([person1, spouse1, spouse2])
        
        # person1 is married to spouse1, but there's another spouse in the household
        # So person1 is married to someone else (spouse2) besides spouse1
        assert is_married_filing_jointly(person1, spouse1, person_df) is False
        
        # Also test the reverse case
        assert is_married_filing_jointly(spouse1, person1, person_df) is False
        
    def test_is_married_to_someone_else_edge_cases(self):
        """Test edge cases for the _is_married_to_someone_else function."""
        # Create a household with a person and their spouse
        person1 = create_test_person('1', '1', 40, married=True, relshipp=20)
        spouse = create_test_person('1', '2', 38, married=True, relshipp=21)
        
        # Create a DataFrame with just these two people
        person_df = pd.DataFrame([person1, spouse])
        
        # Neither should be married to someone else
        assert _is_married_to_someone_else(person1, spouse, person_df) is False
        assert _is_married_to_someone_else(spouse, person1, person_df) is False
        
        # Test with a person from a different household (no SERIALNO match)
        other_household_person = create_test_person('999', '1', 40, married=True, relshipp=20)
        # person1 is married to spouse, so when checking against someone from another household,
        # it should find the spouse and return True
        assert _is_married_to_someone_else(person1, other_household_person, person_df) is True
