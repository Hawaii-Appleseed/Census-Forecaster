"""SPM childcare-expense cap: the lowest-earning adult's earnings (Interagency Technical Working Group,
Observations, March 2010; the Census Bureau's SPM follows it).

Before: two-earner couples were capped at 0.5 x their total earnings, whoever earned what.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.benefits.childcare_expense import compute_childcare_expense_for_units
from tax_modeler.credits.hi_cdcc import earned_income_limit
from tax_modeler.units.earnings import lower_earner_income


def _unit(status="married_filing_jointly", p=70_000.0, s=30_000.0, hours=None, kids=2, income=100_000.0):
    d = {"filing_status": [status], "primary_wagp": [p], "primary_semp": [0.0],
         "secondary_wagp": [s], "secondary_semp": [0.0], "earned_income": [p + s],
         "total_cash_income": [income], "num_qualifying_children": [kids]}
    if hours is not None:
        d["secondary_hours_worked"] = [hours]
    return pd.DataFrame(d)


def _expense(u):
    return float(compute_childcare_expense_for_units(u)["childcare_expense_amount"].iloc[0])


def test_lower_earner_income_by_filing_status():
    u = pd.concat([_unit(p=70_000, s=30_000), _unit(p=50_000, s=0), _unit(status="head_of_household", p=40_000, s=0),
                   _unit(status="married_filing_separately", p=25_000, s=0)], ignore_index=True)
    assert lower_earner_income(u).tolist() == [30_000.0, 0.0, 40_000.0, 25_000.0]


def test_lower_earner_income_keeps_the_aged_total():
    u = _unit(p=60_000, s=20_000)
    u["earned_income"] = 120_000.0                       # aged 1.5x: the lesser spouse's 25% share of it
    assert lower_earner_income(u).tolist() == [30_000.0]


def test_the_credit_limit_and_the_spm_cap_are_one_number():
    u = pd.concat([_unit(p=70_000, s=30_000), _unit(p=50_000, s=0)], ignore_index=True)
    np.testing.assert_array_equal(earned_income_limit(u), lower_earner_income(u))


def test_a_couple_is_capped_at_the_lower_earners_earnings_not_half_the_total():
    """Fails with the 0.5 x total rule: that cap is $50,000 here and never binds."""
    uncapped = _expense(_unit(s=30_000.0))
    assert uncapped > 300.0
    assert _expense(_unit(p=99_700.0, s=300.0)) == pytest.approx(300.0)
    assert _expense(_unit(p=99_700.0, s=300.0, hours=40.0)) == pytest.approx(300.0)


def test_a_one_earner_couple_gets_no_childcare_expense_whatever_the_hours_say():
    assert _expense(_unit(p=100_000.0, s=0.0)) == 0.0
    assert _expense(_unit(p=100_000.0, s=0.0, hours=40.0)) == 0.0


def test_a_second_earner_without_hours_still_counts_on_earnings():
    """Self-employed spouse with earnings but no WKHP: the earnings decide, not the hours."""
    assert _expense(_unit(s=30_000.0, hours=0.0)) == pytest.approx(_expense(_unit(s=30_000.0)))
    assert _expense(_unit(s=30_000.0)) > 0.0


def test_single_parents_are_capped_at_their_own_earnings():
    assert _expense(_unit(status="head_of_household", p=400.0, s=0.0, kids=1, income=400.0)) == pytest.approx(400.0)
    assert _expense(_unit(status="head_of_household", p=60_000.0, s=0.0, kids=1, income=60_000.0)) > 400.0
    assert _expense(_unit(status="head_of_household", p=0.0, s=0.0, kids=1, income=0.0)) == 0.0


def test_a_frame_without_filer_earnings_falls_back_to_the_hours_rule():
    u = _unit(s=30_000.0, hours=40.0).drop(columns=["primary_wagp", "primary_semp", "secondary_wagp", "secondary_semp"])
    assert _expense(u) > 0.0
    assert _expense(u.assign(secondary_hours_worked=0.0)) == 0.0
