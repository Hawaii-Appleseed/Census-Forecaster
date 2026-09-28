"""
Tests for Child Tax Credit (CTC) calculations.
"""

from __future__ import annotations

import pytest
import pandas as pd
import os


from tax_modeler.credits.ctc import (
    calculate_ctc, 
    _get_qualifying_children, 
    _is_qualifying_child_ctc,
    _apply_income_phaseout,
    CTCParameters,
    calculate_ctc_for_tax_units
)


def test_ctc_basic_calculation():
    """Test basic CTC calculation with qualifying children."""
    tax_unit = {
        'filing_status': 'single',
        'income': 50000,
        'earned_income': 30000,  # $30,000 in earned income
        'dependents': [
            {'age': 10, 'relationship': '25', 'citizenship': '1'},  # Qualifying child
            {'age': 15, 'relationship': '25', 'citizenship': '1'}   # Qualifying child
        ],
        'num_dependents': 2
    }
    
    result = calculate_ctc(tax_unit)
    
    assert result['qualifying_children'] == 2
    assert result['ctc_total'] == 4000  # 2 children × $2,000

    # TY2023 single, $50K: tax before credits = 1_100 + 12% × (36_150 − 11_000)
    # = 4_118 ≥ 4_000, so the whole credit offsets tax and none is refunded.
    assert result['ctc_nonrefundable'] == 4000
    assert result['ctc_refundable'] == 0


def test_ctc_no_qualifying_children():
    """Test CTC calculation with no qualifying children."""
    tax_unit = {
        'filing_status': 'single',
        'income': 50000,
        'dependents': [
            {'age': 18, 'relationship': '25', 'citizenship': '1'},  # Too old
        ],
        'num_dependents': 1
    }
    
    result = calculate_ctc(tax_unit)
    
    assert result['qualifying_children'] == 0
    assert result['ctc_total'] == 0
    assert result['ctc_refundable'] == 0
    assert result['ctc_nonrefundable'] == 0


def test_ctc_income_phaseout_single():
    """Test CTC phaseout for single filers."""
    tax_unit = {
        'filing_status': 'single',
        'income': 210000,  # $10,000 over threshold
        'dependents': [
            {'age': 10, 'relationship': '25', 'citizenship': '1'}
        ],
        'num_dependents': 1
    }
    
    result = calculate_ctc(tax_unit)
    
    # Phaseout: $10,000 over threshold = 10 × $50 = $500 reduction
    expected_credit = 2000 - 500
    assert result['ctc_total'] == expected_credit


def test_ctc_income_phaseout_joint():
    """Test CTC phaseout for joint filers."""
    tax_unit = {
        'filing_status': 'married_filing_jointly',
        'income': 420000,  # $20,000 over threshold
        'dependents': [
            {'age': 10, 'relationship': '25', 'citizenship': '1'}
        ],
        'num_dependents': 1
    }
    
    result = calculate_ctc(tax_unit)
    
    # Phaseout: $20,000 over threshold = 20 × $50 = $1,000 reduction
    expected_credit = 2000 - 1000
    assert result['ctc_total'] == expected_credit


def test_ctc_complete_phaseout():
    """Test CTC complete phaseout at high income."""
    tax_unit = {
        'filing_status': 'single',
        'income': 250000,  # $50,000 over threshold
        'dependents': [
            {'age': 10, 'relationship': '25', 'citizenship': '1'}
        ],
        'num_dependents': 1
    }
    
    result = calculate_ctc(tax_unit)
    
    # Phaseout: $50,000 over threshold = 50 × $50 = $2,500 reduction
    # Credit is completely phased out
    assert result['ctc_total'] == 0


def test_qualifying_child_age_limit():
    """Test age limit for qualifying children."""
    params = CTCParameters()
    
    # Under 17 - qualifies
    child_16 = {'age': 16, 'relationship': '25', 'citizenship': '1'}
    assert _is_qualifying_child_ctc(child_16, params) == True
    
    # 17 or over - doesn't qualify
    child_17 = {'age': 17, 'relationship': '25', 'citizenship': '1'}
    assert _is_qualifying_child_ctc(child_17, params) == False


def test_qualifying_relationship():
    """CTC qualifying child uses PUMS RELSHIPP codes (20-38)."""
    params = CTCParameters()

    # Own child (bio 25, adopted 26, step 27), sibling 28, grandchild 30, foster 35.
    for code in ('25', '26', '27', '28', '30', '35', 30):
        dep = {'age': 10, 'relationship': code, 'citizenship': '1'}
        assert _is_qualifying_child_ctc(dep, params), code

    # Unmarried partner 22, same-sex spouse 23, parent-in-law 31, other
    # relative 33 (nieces/nephews not separable), roommate 34, nonrelative 36.
    for code in ('22', '23', '31', '33', '34', '36', '', None):
        dep = {'age': 10, 'relationship': code, 'citizenship': '1'}
        assert not _is_qualifying_child_ctc(dep, params), code


def test_citizenship_requirement():
    """Test citizenship requirement for CTC."""
    params = CTCParameters()
    
    # US citizens qualify
    us_born = {'age': 10, 'relationship': '25', 'citizenship': '1'}
    naturalized = {'age': 10, 'relationship': '25', 'citizenship': '4'}
    
    assert _is_qualifying_child_ctc(us_born, params) == True
    assert _is_qualifying_child_ctc(naturalized, params) == True
    
    # Non-citizens don't qualify
    non_citizen = {'age': 10, 'relationship': '25', 'citizenship': '5'}
    assert _is_qualifying_child_ctc(non_citizen, params) == False


def test_ctc_dataframe_calculation():
    """Test CTC calculation for DataFrame of tax units."""
    tax_units_data = [
        {
            'filer_id': '1',
            'filing_status': 'single',
            'income': 50000,
            'dependents': [{'age': 10, 'relationship': '25', 'citizenship': '1'}],
            'num_dependents': 1
        },
        {
            'filer_id': '2',
            'filing_status': 'married_filing_jointly',
            'income': 80000,
            'dependents': [
                {'age': 8, 'relationship': '25', 'citizenship': '1'},
                {'age': 12, 'relationship': '25', 'citizenship': '1'}
            ],
            'num_dependents': 2
        }
    ]
    
    df = pd.DataFrame(tax_units_data)
    result_df = calculate_ctc_for_tax_units(df)
    
    # Check first tax unit (1 child)
    assert result_df.iloc[0]['ctc_ctc_total'] == 2000
    assert result_df.iloc[0]['ctc_qualifying_children'] == 1
    
    # Check second tax unit (2 children)
    assert result_df.iloc[1]['ctc_ctc_total'] == 4000
    assert result_df.iloc[1]['ctc_qualifying_children'] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


def _kids(n):
    return [{'age': 5 + i, 'relationship': '25', 'citizenship': '1'} for i in range(n)]


def test_ctc_offsets_tax_before_refund():
    """IRC §24(d): refund only the part income tax does not absorb."""
    unit = {'filing_status': 'married_filing_jointly', 'income': 40_000,
            'earned_income': 40_000, 'dependents': _kids(2)}
    r = calculate_ctc(unit, tax_year=2024)
    # Tax = 10% × (40_000 − 29_200) = 1_080; remainder 2_920 < cap 3_400.
    assert r['ctc_nonrefundable'] == pytest.approx(1_080)
    assert r['ctc_refundable'] == pytest.approx(2_920)
    assert r['ctc_total'] == pytest.approx(4_000)


def test_actc_earned_income_limit_is_per_return():
    """15% × (EI − 2_500) is one amount per return, not per child."""
    unit = {'filing_status': 'head_of_household', 'income': 10_000,
            'earned_income': 10_000, 'dependents': _kids(3)}
    r = calculate_ctc(unit, tax_year=2024)
    assert r['ctc_nonrefundable'] == 0  # below the standard deduction
    assert r['ctc_refundable'] == pytest.approx(0.15 * 7_500)


def test_ctc_uses_supplied_tax_before_credits():
    unit = {'filing_status': 'single', 'income': 30_000, 'earned_income': 30_000,
            'dependents': _kids(1), 'federal_tax_before_credits': 500.0}
    r = calculate_ctc(unit, tax_year=2024)
    assert r['ctc_nonrefundable'] == pytest.approx(500)
    assert r['ctc_refundable'] == pytest.approx(1_500)


def test_ctc_2025_max_credit_2200():
    unit = {'filing_status': 'married_filing_jointly', 'income': 150_000,
            'earned_income': 150_000, 'dependents': _kids(2)}
    assert calculate_ctc(unit, tax_year=2025)['ctc_total'] == 4_400
    assert calculate_ctc(unit, tax_year=2024)['ctc_total'] == 4_000


def test_ctc_filer_ssn_required_from_2025():
    unit = {'filing_status': 'married_filing_jointly', 'income': 60_000,
            'earned_income': 60_000, 'dependents': _kids(1), 'filer_has_ssn': False}
    assert calculate_ctc(unit, tax_year=2025)['ctc_total'] == 0
    assert calculate_ctc(unit, tax_year=2024)['ctc_total'] == 2_000
    unit['filer_has_ssn'] = True
    assert calculate_ctc(unit, tax_year=2025)['ctc_total'] == 2_200
