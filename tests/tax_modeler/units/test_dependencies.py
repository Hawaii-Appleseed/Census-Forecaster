"""
Tests for the dependencies module.
"""

from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.units.dependencies import (
    _is_qualifying_child,
    _is_qualifying_relative,
    identify_dependents,
)


# Test data — RELSHIPP uses canonical Census 2019+ PUMS integer codes:
#   20 = Reference person, 21 = Spouse, 25 = Biological child,
#   29 = Parent, 35 = Foster child, 36 = Other nonrelative.
def create_test_household():
    """Create a test household with various members."""
    # Create household members
    members = [
        # Primary filer (adult)
        {'SERIALNO': '1', 'SPORDER': '1', 'AGEP': 35, 'SEX': 1, 'MAR': 5, 'RELSHIPP': 20, 'SCHL': 16, 'SCH': 0, 'WAGP': 50000},
        # Spouse
        {'SERIALNO': '1', 'SPORDER': '2', 'AGEP': 33, 'SEX': 2, 'MAR': 1, 'RELSHIPP': 21, 'SCHL': 16, 'SCH': 0, 'WAGP': 45000},
        # Qualifying child 1 (under 19, biological child). SCH=2 = enrolled
        # (public school) per real PUMS coding; SCH=1 means NOT enrolled.
        {'SERIALNO': '1', 'SPORDER': '3', 'AGEP': 10, 'SEX': 1, 'MAR': 0, 'RELSHIPP': 25, 'SCHL': 3, 'SCH': 2, 'WAGP': 0},
        # Qualifying child 2 (enrolled student under 24, biological child)
        {'SERIALNO': '1', 'SPORDER': '4', 'AGEP': 20, 'SEX': 2, 'MAR': 0, 'RELSHIPP': 25, 'SCHL': 16, 'SCH': 2, 'WAGP': 5000},
        # Non-qualifying person (unrelated adult, other nonrelative)
        {'SERIALNO': '1', 'SPORDER': '5', 'AGEP': 25, 'SEX': 1, 'MAR': 0, 'RELSHIPP': 36, 'SCHL': 16, 'SCH': 0, 'WAGP': 30000},
        # Qualifying relative (elderly parent)
        {'SERIALNO': '1', 'SPORDER': '6', 'AGEP': 70, 'SEX': 2, 'MAR': 3, 'RELSHIPP': 29, 'SCHL': 10, 'SCH': 0, 'WAGP': 10000},
        # Foster child
        {'SERIALNO': '1', 'SPORDER': '7', 'AGEP': 12, 'SEX': 1, 'MAR': 0, 'RELSHIPP': 35, 'SCHL': 4, 'SCH': 2, 'WAGP': 0},
    ]
    
    # Create DataFrame and set index
    df = pd.DataFrame(members)
    df['person_id'] = df['SERIALNO'] + '_' + df['SPORDER'].astype(str)
    df.set_index('person_id', inplace=True)
    
    return df

class TestDependencies:
    """Test cases for dependency identification."""
    
    def test_identify_dependents(self):
        """Test identification of all dependents in a household."""
        hh_df = create_test_household()
        dependents = identify_dependents(hh_df)
        
        # Primary filer should have dependents
        assert '1_1' in dependents
        assert len(dependents['1_1']) > 0
        
        # Should have identified all qualifying children and relatives
        # Primary filer's dependents should include children (1_3, 1_4, 1_7) and possibly relative (1_6)
        assert '1_3' in dependents['1_1']  # Child under 19
        assert '1_4' in dependents['1_1']  # Student under 24
        assert '1_7' in dependents['1_1']  # Foster child
        
        # Spouse might also be able to claim some dependents
        assert '1_2' in dependents
    
    def test_is_qualifying_child_positive(self):
        """Test identification of a qualifying child."""
        hh_df = create_test_household()
        parent = hh_df.loc['1_1']  # Primary filer
        child = hh_df.loc['1_3']   # Child under 19
        
        assert _is_qualifying_child(child, parent, hh_df) is True
    
    def test_is_qualifying_child_negative_age(self):
        """Test that a too-old non-student is not a qualifying child."""
        hh_df = create_test_household()
        parent = hh_df.loc['1_1']
        
        # Create a 25-year-old non-student
        old_child = hh_df.loc['1_4'].copy()
        old_child['AGEP'] = 25
        old_child['SCH'] = 0
        
        assert _is_qualifying_child(old_child, parent, hh_df) is False
    
    def test_is_qualifying_child_negative_relationship(self):
        """Test that unrelated individuals are not qualifying children."""
        hh_df = create_test_household()
        parent = hh_df.loc['1_1']  # Primary filer
        unrelated = hh_df.loc['1_5']  # Unrelated individual
        
        assert _is_qualifying_child(unrelated, parent, hh_df) is False
    
    def test_is_qualifying_relative_positive(self):
        """Test identification of a qualifying relative."""
        hh_df = create_test_household()
        filer = hh_df.loc['1_1']  # Primary filer
        relative = hh_df.loc['1_6']  # Elderly parent
        
        # Make relative's income low enough to qualify
        relative['WAGP'] = 4000
        
        assert _is_qualifying_relative(relative, filer, hh_df) is True
    
    def test_is_qualifying_relative_negative_income(self):
        """Test that high-income individuals are not qualifying relatives."""
        hh_df = create_test_household()
        filer = hh_df.loc['1_1']
        relative = hh_df.loc['1_6']
        
        # Make relative's income too high
        relative['WAGP'] = 5000  # Above the threshold
        
        assert _is_qualifying_relative(relative, filer, hh_df) is False
    
    def test_is_qualifying_relative_negative_relationship(self):
        """Test that unrelated individuals are not qualifying relatives."""
        hh_df = create_test_household()
        filer = hh_df.loc['1_1']
        unrelated = hh_df.loc['1_5']  # Unrelated individual

        assert _is_qualifying_relative(unrelated, filer, hh_df) is False

    def test_elderly_parent_over_income_limit_not_attached(self):
        """Elderly parent earning above the gross-income limit is not a
        qualifying relative and is attached to no one (files for themselves)."""
        hh_df = create_test_household()  # 1_6 is parent (RELSHIPP 29), WAGP 10000
        dependents = identify_dependents(hh_df)
        assert all('1_6' not in deps for deps in dependents.values())

    def test_roommate_not_attached(self):
        """An unrelated working roommate (RELSHIPP 36) is never a dependent."""
        hh_df = create_test_household()  # 1_5 is roommate, WAGP 30000
        dependents = identify_dependents(hh_df)
        assert all('1_5' not in deps for deps in dependents.values())


def _household(members):
    df = pd.DataFrame(members)
    df['person_id'] = df['SERIALNO'] + '_' + df['SPORDER'].astype(str)
    return df.set_index('person_id')


class TestAdultDependentAttachment:
    """Adult relatives who should be claimed as dependents, not split into
    their own childless tax units (the over-splitting fix)."""

    def test_adult_child_qualifying_relative_attached(self):
        """A non-student adult child (age >= 24) earning under the gross-income
        limit is a qualifying relative of the householder and is attached."""
        hh = _household([
            {'SERIALNO': '9', 'SPORDER': 1, 'AGEP': 58, 'SEX': 1, 'MAR': 1,
             'RELSHIPP': 20, 'SCHL': 21, 'SCH': 0, 'WAGP': 70000},
            {'SERIALNO': '9', 'SPORDER': 2, 'AGEP': 27, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 25, 'SCHL': 19, 'SCH': 0, 'WAGP': 3000},
        ])
        dependents = identify_dependents(hh)
        assert '9_2' in dependents['9_1']

    def test_working_adult_child_over_limit_independent(self):
        """An adult child earning above the gross-income limit is NOT a
        dependent and remains an independent filer."""
        hh = _household([
            {'SERIALNO': '10', 'SPORDER': 1, 'AGEP': 58, 'SEX': 1, 'MAR': 1,
             'RELSHIPP': 20, 'SCHL': 21, 'SCH': 0, 'WAGP': 70000},
            {'SERIALNO': '10', 'SPORDER': 2, 'AGEP': 30, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 25, 'SCHL': 19, 'SCH': 0, 'WAGP': 40000},
        ])
        dependents = identify_dependents(hh)
        assert all('10_2' not in deps for deps in dependents.values())

    def test_student_adult_child_with_part_time_job_is_dependent(self):
        """A 20-year-old student child with a modest part-time job stays a
        qualifying child (below the student self-support floor)."""
        hh = _household([
            {'SERIALNO': '11', 'SPORDER': 1, 'AGEP': 50, 'SEX': 1, 'MAR': 1,
             'RELSHIPP': 20, 'SCHL': 21, 'SCH': 0, 'WAGP': 60000},
            # SCH=2 = enrolled (public school) per real PUMS coding.
            {'SERIALNO': '11', 'SPORDER': 2, 'AGEP': 20, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 25, 'SCHL': 16, 'SCH': 2, 'WAGP': 8000},
        ])
        dependents = identify_dependents(hh)
        assert '11_2' in dependents['11_1']

    def test_enrolled_student_over_self_support_floor_is_independent(self):
        """Regression (audit B4): an ENROLLED 20-year-old earning above the
        student self-support floor ($20k) provides over half their own support
        and is NOT a dependent — they file for themselves. Previously the
        18-23 path applied no support test, so any working student child was
        folded into the parent's unit."""
        hh = _household([
            {'SERIALNO': '13', 'SPORDER': 1, 'AGEP': 50, 'SEX': 1, 'MAR': 1,
             'RELSHIPP': 20, 'SCHL': 21, 'SCH': 0, 'WAGP': 60000},
            {'SERIALNO': '13', 'SPORDER': 2, 'AGEP': 20, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 25, 'SCHL': 16, 'SCH': 2, 'WAGP': 35000},  # enrolled, $35k
        ])
        dependents = identify_dependents(hh)
        assert all('13_2' not in deps for deps in dependents.values())

    def test_sch_enrollment_direction(self):
        """Regression (audit B2): SCH=1 means NOT enrolled (per real PUMS).

        Income ($10k) is chosen to isolate the enrollment effect: above the
        qualifying-relative gross-income limit (~$4.7k, so the non-student path
        cannot attach them) but below the student self-support floor ($20k).
        Enrolled (SCH=2) ⇒ qualifying child ⇒ dependent; not enrolled (SCH=1)
        ⇒ independent filer. The inverted SCH==1-means-student bug flipped this.
        """
        base = {'SERIALNO': '14', 'SPORDER': 1, 'AGEP': 50, 'SEX': 1, 'MAR': 1,
                'RELSHIPP': 20, 'SCHL': 21, 'SCH': 0, 'WAGP': 60000}
        child = {'SERIALNO': '14', 'SPORDER': 2, 'AGEP': 20, 'SEX': 2, 'MAR': 5,
                 'RELSHIPP': 25, 'SCHL': 16, 'WAGP': 10000}
        not_enrolled = identify_dependents(_household([base, {**child, 'SCH': 1}]))
        enrolled = identify_dependents(_household([base, {**child, 'SCH': 2}]))
        assert all('14_2' not in deps for deps in not_enrolled.values())
        assert '14_2' in enrolled['14_1']

    @pytest.mark.parametrize("income,year_qualifies", [
        (4_800, {2023: False, 2024: True}),
    ])
    def test_year_aware_gross_income_limit(self, income, year_qualifies):
        """The gross-income limit is year-aware: $4,800 disqualifies in TY2023
        ($4,700 limit) but qualifies in TY2024 ($5,050 limit)."""
        hh = _household([
            {'SERIALNO': '12', 'SPORDER': 1, 'AGEP': 60, 'SEX': 1, 'MAR': 1,
             'RELSHIPP': 20, 'SCHL': 21, 'SCH': 0, 'WAGP': 70000},
            {'SERIALNO': '12', 'SPORDER': 2, 'AGEP': 28, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 26, 'SCHL': 19, 'SCH': 0, 'WAGP': income},
        ])
        for year, should_qualify in year_qualifies.items():
            deps = identify_dependents(hh, tax_year=year)
            attached = '12_2' in deps['12_1']
            assert attached is should_qualify, f"year={year}"


class TestCanonicalRelationshipAttachment:
    """Regression tests for the RELSHIPP relationship-code fix: grandchildren,
    stepchildren and foster children must attach to the householder; partners
    and roommates never do."""

    def test_grandchild_attaches_to_grandparent_householder(self):
        """The headline bug: a grandchild (RELSHIPP 30) under 19 living with a
        grandparent householder must attach as that householder's qualifying
        child. Before the fix, code 30 was never recognized as a child."""
        hh = _household([
            {'SERIALNO': 'G1', 'SPORDER': 1, 'AGEP': 60, 'SEX': 2, 'MAR': 3,
             'RELSHIPP': 20, 'SCHL': 18, 'SCH': 0, 'WAGP': 35000},
            {'SERIALNO': 'G1', 'SPORDER': 2, 'AGEP': 12, 'SEX': 1, 'MAR': 0,
             'RELSHIPP': 30, 'SCHL': 6, 'SCH': 1, 'WAGP': 0},
        ])
        deps = identify_dependents(hh)
        assert 'G1_2' in deps['G1_1']

    def test_stepchild_attaches_to_householder(self):
        """A stepchild (RELSHIPP 27) under 19 attaches as a qualifying child."""
        hh = _household([
            {'SERIALNO': 'ST1', 'SPORDER': 1, 'AGEP': 40, 'SEX': 1, 'MAR': 1,
             'RELSHIPP': 20, 'SCHL': 18, 'SCH': 0, 'WAGP': 55000},
            {'SERIALNO': 'ST1', 'SPORDER': 2, 'AGEP': 9, 'SEX': 2, 'MAR': 0,
             'RELSHIPP': 27, 'SCHL': 3, 'SCH': 1, 'WAGP': 0},
        ])
        deps = identify_dependents(hh)
        assert 'ST1_2' in deps['ST1_1']

    def test_adult_parent_under_income_limit_attaches_as_relative(self):
        """An adult parent (RELSHIPP 29) earning under the gross-income limit
        attaches as a qualifying relative of the householder."""
        hh = _household([
            {'SERIALNO': 'P1', 'SPORDER': 1, 'AGEP': 50, 'SEX': 1, 'MAR': 5,
             'RELSHIPP': 20, 'SCHL': 21, 'SCH': 0, 'WAGP': 80000},
            {'SERIALNO': 'P1', 'SPORDER': 2, 'AGEP': 78, 'SEX': 2, 'MAR': 3,
             'RELSHIPP': 29, 'SCHL': 16, 'SCH': 0, 'WAGP': 2000},
        ])
        deps = identify_dependents(hh)
        assert 'P1_2' in deps['P1_1']

    def test_roommate_code_34_never_attaches(self):
        """A working roommate (RELSHIPP 34) is never a dependent."""
        hh = _household([
            {'SERIALNO': 'R1', 'SPORDER': 1, 'AGEP': 35, 'SEX': 1, 'MAR': 5,
             'RELSHIPP': 20, 'SCHL': 21, 'SCH': 0, 'WAGP': 60000},
            {'SERIALNO': 'R1', 'SPORDER': 2, 'AGEP': 30, 'SEX': 2, 'MAR': 5,
             'RELSHIPP': 34, 'SCHL': 19, 'SCH': 0, 'WAGP': 32000},
        ])
        deps = identify_dependents(hh)
        assert all('R1_2' not in d for d in deps.values())
