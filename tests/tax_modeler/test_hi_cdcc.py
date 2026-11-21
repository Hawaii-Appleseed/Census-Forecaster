"""HRS §235-55.6 (child and dependent care credit): the statutory schedule, Act 163's dollar limits and
their December 31, 2027 repeal, and the expected-credit calculation over donor expenses.

Worked examples apply the statute's text: credit = applicable percentage (a)(2) x the least of the
expenses paid, the dollar limit (c) and the earned income limit (d); refundable, nothing under $1.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.credits import hi_cdcc as C


@pytest.mark.parametrize("agi,pct", [
    (0, 0.25), (25_000, 0.25),              # "Not over $25,000"
    (25_001, 0.24), (30_000, 0.24),         # "Over $25,000 but not over $30,000"
    (30_001, 0.23), (35_000, 0.23),
    (35_001, 0.22), (40_000, 0.22),
    (40_001, 0.21), (45_000, 0.21),
    (45_001, 0.20), (50_000, 0.20),
    (50_001, 0.15), (400_000, 0.15),        # "Over $50,000": 15%, with no phase-out
])
def test_applicable_percentage_schedule(agi, pct):
    assert C.applicable_percentage(agi) == pytest.approx(pct)


@pytest.mark.parametrize("year,caps", [
    (2022, (2_400, 4_800)),                 # before Act 163
    (2023, (10_000, 20_000)), (2027, (10_000, 20_000)),
    (2028, (2_400, 4_800)), (2031, (2_400, 4_800)),   # (c) reenacted as it read before
])
def test_dollar_limits_by_year(year, caps):
    assert C.caps_for_year(year) == caps


def credit(agi, n, expenses, earned, caps, **kw):
    return float(C.cdcc_credit(np.array([agi]), np.array([n]), np.array([expenses]),
                               np.array([earned]), caps, **kw)[0])


@pytest.mark.parametrize("agi,n,expenses,earned,act163,prior", [
    # one child, AGI $20,000 (25%), $6,000 paid: $10,000 limit vs $2,400
    (20_000, 1, 6_000, 30_000, 0.25 * 6_000, 0.25 * 2_400),
    # AGI $42,000 is "over $40,000 but not over $45,000": 21%
    (42_000, 1, 3_000, 50_000, 0.21 * 3_000, 0.21 * 2_400),
    # two children, AGI $60,000 (15%), $15,000 paid, the lesser earner made $12,000: the earned income limit binds
    # under Act 163; the $4,800 limit binds before it
    (60_000, 2, 15_000, 12_000, 0.15 * 12_000, 0.15 * 4_800),
    # same family, lesser earner made $18,000: the expenses paid bind under Act 163
    (60_000, 2, 15_000, 18_000, 0.15 * 15_000, 0.15 * 4_800),
    # earned income below both limits
    (30_000, 2, 9_000, 1_500, 0.24 * 1_500, 0.24 * 1_500),
    # expenses below both limits: the same credit under either law
    (20_000, 1, 1_000, 40_000, 0.25 * 1_000, 0.25 * 1_000),
    # no qualifying individual
    (20_000, 0, 5_000, 40_000, 0.0, 0.0),
])
def test_worked_examples(agi, n, expenses, earned, act163, prior):
    assert credit(agi, n, expenses, earned, C.ACT163_CAPS) == pytest.approx(act163)
    assert credit(agi, n, expenses, earned, C.PRIOR_CAPS) == pytest.approx(prior)


def test_two_or_more_qualifying_individuals_double_the_limit():
    assert credit(20_000, 1, 30_000, 99_999, C.ACT163_CAPS) == pytest.approx(0.25 * 10_000)
    assert credit(20_000, 2, 30_000, 99_999, C.ACT163_CAPS) == pytest.approx(0.25 * 20_000)
    assert credit(20_000, 3, 30_000, 99_999, C.ACT163_CAPS) == pytest.approx(0.25 * 20_000)
    assert credit(20_000, 2, 30_000, 99_999, C.PRIOR_CAPS) == pytest.approx(0.25 * 4_800)


def test_section_129_exclusion_reduces_the_limit():
    # $10,000 limit less $5,000 of employer dependent care assistance
    assert credit(20_000, 1, 10_000, 99_999, C.ACT163_CAPS, dcap_exclusion=5_000) == pytest.approx(0.25 * 5_000)
    assert credit(20_000, 1, 10_000, 99_999, C.PRIOR_CAPS, dcap_exclusion=5_000) == 0.0


def test_credits_under_a_dollar_are_not_paid():
    assert credit(20_000, 1, 3, 99_999, C.ACT163_CAPS) == 0.0      # 25% of $3 = $0.75
    assert credit(20_000, 1, 4, 99_999, C.ACT163_CAPS) == 1.0


def test_the_expansion_is_only_the_limit():
    """Act 163 changed subsection (c) alone: the same expenses under the same percentage."""
    for agi in (10_000, 33_000, 48_000, 80_000, 250_000):
        big = credit(agi, 1, 2_000, 1e6, C.ACT163_CAPS)
        small = credit(agi, 1, 2_000, 1e6, C.PRIOR_CAPS)
        assert big == small                                            # $2,000 is under both limits


def test_children_under_13_counts_the_dependents_the_credit_names():
    deps = [{"age": 2}, {"age": 12}, {"age": 13}, {"age": 16}, {"age": None}, {}]
    assert C.count_children_under_13(deps) == 2
    assert C.count_children_under_13(None) == 0


def _frame(**rows):
    return pd.DataFrame(rows)


def test_earned_income_limit_is_the_lesser_spouses_for_joint_returns():
    u = _frame(filing_status=["single", "married_filing_jointly", "married_filing_jointly", "head_of_household"],
               earned_income=[40_000.0, 100_000.0, 60_000.0, 25_000.0],
               primary_wagp=[40_000, 60_000, 60_000, 25_000], primary_semp=[0, 0, 0, 0],
               secondary_wagp=[0, 20_000, 0, 0], secondary_semp=[0, 0, 0, 0])
    got = C.earned_income_limit(u)
    # joint, $60k and $20k in base-year dollars, aged total $100k: the lesser spouse's share (25%) of the total
    assert got == pytest.approx([40_000, 25_000, 0, 25_000])


@pytest.fixture(autouse=True)
def _everyone_pays(monkeypatch):
    """These tests use toy donors whose weights already say how many families pay; the AGI-class payment probability is
    tested in test_hi_cdcc_pay_rates.py."""
    from tax_modeler.credits import hi_cdcc
    monkeypatch.setattr(hi_cdcc, "pay_rates", lambda: {"edges": [50_000.0], "rates": [1.0, 1.0]})


DONORS = {1: (np.array([3_000.0, 12_000.0]), np.array([0.10, 0.05])),     # 85% pay nothing
          2: (np.array([6_000.0]), np.array([0.20]))}


def _unit(deps, status="head_of_household", agi=20_000.0, earned=30_000.0):
    return pd.DataFrame({
        "dependents_details": [[{"age": a} for a in deps]], "filing_status": [status], "federal_agi": [agi],
        "earned_income": [earned], "primary_wagp": [earned], "primary_semp": [0.0],
        "secondary_wagp": [0.0], "secondary_semp": [0.0]})


def test_expected_credit_is_the_mean_over_the_donors_spending():
    r = C.compute_hi_cdcc_expected(_unit([4]), donors=DONORS).iloc[0]
    exp = 0.10 * credit(20_000, 1, 3_000, 30_000, C.ACT163_CAPS) + 0.05 * credit(20_000, 1, 12_000, 30_000, C.ACT163_CAPS)
    rev = 0.10 * credit(20_000, 1, 3_000, 30_000, C.PRIOR_CAPS) + 0.05 * credit(20_000, 1, 12_000, 30_000, C.PRIOR_CAPS)
    assert r["cdcc_expanded"] == pytest.approx(exp) and exp == pytest.approx(0.25 * (0.1 * 3_000 + 0.05 * 10_000))
    assert r["cdcc_reverted"] == pytest.approx(rev) and rev == pytest.approx(0.25 * 2_400 * 0.15)
    assert r["cdcc_expanded_claim_prob"] == pytest.approx(0.15)
    assert r["cdcc_loss_prob"] == pytest.approx(0.15)          # both donors spend over $2,400


def test_two_children_use_the_two_or_more_donors():
    r = C.compute_hi_cdcc_expected(_unit([3, 9, 15]), donors=DONORS).iloc[0]
    assert r["cdcc_expanded"] == pytest.approx(0.20 * credit(20_000, 2, 6_000, 30_000, C.ACT163_CAPS))
    assert r["cdcc_reverted"] == pytest.approx(0.20 * 0.25 * 4_800)


@pytest.mark.parametrize("unit", [
    _unit([14, 17]),                                              # no child under 13
    _unit([5], earned=0.0),                                       # no earned income
    _unit([5], status="married_filing_separately"),               # §235-55.6(e)(2): joint return required
])
def test_ineligible_units_get_nothing(unit):
    r = C.compute_hi_cdcc_expected(unit, donors=DONORS).iloc[0]
    assert r["cdcc_expanded"] == 0.0 and r["cdcc_reverted"] == 0.0 and r["cdcc_expanded_claim_prob"] == 0.0


def test_a_cell_mean_would_show_no_expansion():
    """Why expenses are a distribution: the donors' mean spending sits under the old limit."""
    mean = sum(x * p for x, p in zip(*DONORS[1], strict=True))              # $900
    assert mean < C.PRIOR_CAPS[0]
    same = credit(20_000, 1, mean, 30_000, C.ACT163_CAPS) == credit(20_000, 1, mean, 30_000, C.PRIOR_CAPS)
    assert same
    r = C.compute_hi_cdcc_expected(_unit([4]), donors=DONORS).iloc[0]
    assert r["cdcc_expanded"] > r["cdcc_reverted"]


def test_donor_distribution_from_the_cps_file():
    d = C.donor_expense_distribution()
    assert set(d) == {1, 2}
    for x, p in d.values():
        assert (x > 0).all() and p.sum() == pytest.approx(1.0) and len(x) == len(p)      # conditional on paying
    # the pooled CPS sample behind it (ASEC 2022-2025): 123 paying families with a child under 13
    assert len(d[1][0]) + len(d[2][0]) == 123


def test_compute_for_units_uses_the_law_of_the_year():
    u = _unit([4])
    a = C.compute_hi_cdcc_for_units(u, tax_year=2025, donors=DONORS)["hi_cdcc_amount"].iloc[0]
    b = C.compute_hi_cdcc_for_units(u, tax_year=2028, donors=DONORS)["hi_cdcc_amount"].iloc[0]
    assert a > b > 0
