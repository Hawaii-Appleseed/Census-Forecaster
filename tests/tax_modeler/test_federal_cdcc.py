"""The minimal federal §21 credit used only to validate the expense model against IRS Table 2 (TY2023 rules)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.credits.federal_cdcc import applicable_percentage, federal_cdcc_expected


@pytest.fixture(autouse=True)
def _everyone_pays(monkeypatch):
    """These tests use toy donors whose weights already say how many families pay; the AGI-class payment probability is
    tested in test_hi_cdcc_pay_rates.py."""
    from tax_modeler.credits import hi_cdcc
    monkeypatch.setattr(hi_cdcc, "pay_rates", lambda: {"edges": [50_000.0], "rates": [1.0, 1.0]})


DONORS = {1: (np.array([2_000.0]), np.array([0.10])), 2: (np.array([9_000.0]), np.array([0.20]))}


@pytest.mark.parametrize("agi,pct", [(0, 0.35), (15_000, 0.35), (15_001, 0.34), (17_000, 0.34), (17_001, 0.33),
                                     (41_000, 0.22), (43_000, 0.21), (43_001, 0.20), (400_000, 0.20)])
def test_percentage_schedule(agi, pct):
    assert applicable_percentage(agi) == pytest.approx(pct)


def _unit(n=1, agi=60_000.0, tax=10_000.0, earn=(50_000.0, 0.0), status="head_of_household"):
    return pd.DataFrame({"num_children_under_13": [float(n)], "federal_agi": [agi], "federal_tax_before_credits": [tax],
                         "filing_status": [status], "earned_income": [earn[0] + earn[1]],
                         "primary_wagp": [earn[0]], "primary_semp": [0.0], "secondary_wagp": [earn[1]], "secondary_semp": [0.0]})


def test_expected_credit_over_the_donors_spending():
    r = federal_cdcc_expected(_unit(), donors=DONORS).iloc[0]
    assert r["federal_cdcc"] == pytest.approx(0.10 * 0.20 * 2_000)             # 20% of $2,000, paid by 10% of families
    assert r["federal_cdcc_claim_prob"] == pytest.approx(0.10)


def test_the_limit_is_3000_for_one_and_6000_for_two_or_more():
    one = federal_cdcc_expected(_unit(1), donors={1: (np.array([9_000.0]), np.array([1.0])), 2: DONORS[2]}).iloc[0]
    two = federal_cdcc_expected(_unit(2), donors={1: DONORS[1], 2: (np.array([9_000.0]), np.array([1.0]))}).iloc[0]
    assert one["federal_cdcc"] == pytest.approx(0.20 * 3_000) and two["federal_cdcc"] == pytest.approx(0.20 * 6_000)


def test_it_is_non_refundable_and_limited_to_the_lower_earner():
    assert federal_cdcc_expected(_unit(tax=100.0), donors=DONORS).iloc[0]["federal_cdcc"] == pytest.approx(0.10 * 100.0)
    assert federal_cdcc_expected(_unit(tax=0.0), donors=DONORS).iloc[0]["federal_cdcc"] == 0.0
    joint = _unit(status="married_filing_jointly", earn=(80_000.0, 500.0))
    assert federal_cdcc_expected(joint, donors=DONORS).iloc[0]["federal_cdcc"] == pytest.approx(0.10 * 0.20 * 500.0)


def test_no_credit_without_a_child_under_13_or_for_married_filing_separately():
    assert federal_cdcc_expected(_unit(0), donors=DONORS).iloc[0]["federal_cdcc"] == 0.0
    assert federal_cdcc_expected(_unit(status="married_filing_separately"), donors=DONORS).iloc[0]["federal_cdcc"] == 0.0
