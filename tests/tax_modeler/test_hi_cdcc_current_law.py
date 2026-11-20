"""The credit calculator's care credit (HawaiiTaxCredits): the statute's schedule, by tax year, times the
expected expenses over CPS ASEC donor spending, for current law and both bills; and the inputs the scoring path
feeds it. The kernel's tables carry the same."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.adjustments.hawaii_credits import (
    HawaiiTaxCredits,
    apply_credits_to_dataframe,
    calculate_hawaii_credits,
)
from tax_modeler.config.tax_system_config import TaxCalculator, care_credit_inputs
from tax_modeler.credits import hi_cdcc
from tax_modeler.simulator.systems import food_excise_tables

DONORS = hi_cdcc.donor_expense_distribution()


def expected_expense(n, caps, earned=np.inf, agi=0.0):
    """The definition, written out: the probability of paying at this AGI times the paying donors' mean of
    min(spending, limit, earnings)."""
    x, p = DONORS[1 if n == 1 else 2]
    cap = caps[0] if n == 1 else caps[1]
    return float(hi_cdcc.pay_probability(agi)) * float(sum(pj * min(xj, cap, earned) for xj, pj in zip(x, p, strict=True)))


@pytest.mark.parametrize("year", [2022, 2023, 2025, 2027, 2028, 2031])
@pytest.mark.parametrize("agi", [0, 20_000, 25_000, 25_001, 47_000, 50_000, 50_001, 120_000, 500_000])
@pytest.mark.parametrize("deps", [1, 2, 4])
def test_current_law_is_the_statute_times_expected_expenses(year, agi, deps):
    got = HawaiiTaxCredits(year).child_dependent_care_credit(agi, "single", deps)
    want = float(hi_cdcc.applicable_percentage(agi)) * expected_expense(deps, hi_cdcc.caps_for_year(year), agi=agi)
    assert got == pytest.approx(want, rel=1e-12)


def test_the_limits_follow_the_tax_year():
    """Act 163 §1 raised the limits for TY2023-2027; §5 repeals and reenacts subsection (c) after 2027."""
    kw = dict(agi=20_000, filing_status="single", num_dependents=1)
    by_year = {y: HawaiiTaxCredits(y).child_dependent_care_credit(**kw) for y in (2022, 2023, 2027, 2028, 2031)}
    assert by_year[2023] == by_year[2027] > by_year[2028] == by_year[2031] == by_year[2022] > 0


def test_expected_expenses_are_not_every_filer_hitting_the_limit():
    """The credit is a small share of percentage x limit: only some families pay, and few pay a lot."""
    c = HawaiiTaxCredits(2027).child_dependent_care_credit(20_000, "single", 1)
    assert 0 < c < 0.25 * 10_000 * 0.2
    x, p = DONORS[1]
    assert hi_cdcc.pay_probability(20_000) < 0.25 and (x > 10_000).any()


def test_only_children_under_13_with_earnings_qualify():
    c = HawaiiTaxCredits(2027)
    base = c.child_dependent_care_credit(30_000, "head_of_household", 2, care_children=2, care_earned=np.inf)
    assert base > 0
    assert c.child_dependent_care_credit(30_000, "head_of_household", 2, care_children=0, care_earned=np.inf) == 0
    assert c.child_dependent_care_credit(30_000, "head_of_household", 2, care_children=2, care_earned=0.0) == 0
    capped = c.child_dependent_care_credit(30_000, "head_of_household", 2, care_children=2, care_earned=300.0)
    assert 0 < capped < base
    one = c.child_dependent_care_credit(30_000, "head_of_household", 2, care_children=1, care_earned=np.inf)
    assert one != base                                           # one-child donors and limit, not two


def test_no_inputs_counts_every_dependent_with_no_earnings_limit():
    c = HawaiiTaxCredits(2027)
    assert (c.child_dependent_care_credit(30_000, "single", 2)
            == c.child_dependent_care_credit(30_000, "single", 2, care_children=2, care_earned=np.inf))


def test_the_credit_is_paid_when_there_is_no_tax():
    r = HawaiiTaxCredits(2027).calculate_total_credits(30_000, "head_of_household", 2, 0.0)
    assert r["child_care"] > 0 and r["total_refundable"] >= r["child_care"] and r["total"] >= r["child_care"]


def test_bills_keep_their_percentages_and_limits_and_use_the_same_expenses():
    c = HawaiiTaxCredits(2027)
    hb = c.child_dependent_care_credit(50_000, "head_of_household", 2, credit_scenario="hb2306_hd1")
    assert hb == pytest.approx(0.50 * expected_expense(2, (10_000, 20_000), agi=50_000), rel=1e-12)
    sd1 = c.child_dependent_care_credit(43_000, "single", 1, credit_scenario="sb3125_sd1")
    assert sd1 == pytest.approx(0.35 * expected_expense(1, (10_000, 20_000), agi=43_000), rel=1e-12)
    # the bills' limits do not revert in 2028: only current law's do
    c28 = HawaiiTaxCredits(2028)
    assert c28.child_dependent_care_credit(43_000, "single", 1, credit_scenario="sb3125_sd1") == sd1
    assert c28.child_dependent_care_credit(43_000, "single", 1) < c.child_dependent_care_credit(43_000, "single", 1)


def test_the_convenience_function_and_the_dataframe_path_take_the_year():
    kw = dict(agi=20_000, filing_status="single", num_dependents=1, tax_before_credits=0)
    assert calculate_hawaii_credits(year=2027, **kw)["child_care"] > calculate_hawaii_credits(year=2028, **kw)["child_care"]
    df = pd.DataFrame({"agi": [20_000.0], "filing_status": ["single"], "num_dependents": [1], "tax_liability": [0.0]})
    assert (apply_credits_to_dataframe(df, year=2027)["hi_credit_child_care"].iloc[0]
            > apply_credits_to_dataframe(df, year=2028)["hi_credit_child_care"].iloc[0])


def test_arrays_take_the_unit_inputs_and_match_the_scalar_method():
    c = HawaiiTaxCredits(2027)
    agi = np.array([20_000.0, 60_000.0, 30_000.0, 30_000.0])
    status = np.array(["single", "married_filing_jointly", "head_of_household", "head_of_household"], dtype=object)
    deps = np.array([1, 2, 2, 2])
    kids = np.array([1.0, 2.0, 0.0, 2.0])
    earn = np.array([np.inf, 40_000.0, np.inf, 250.0])
    total, cc = c.total_credits_arrays(agi, status, deps, np.array([0.0, 500.0, 500.0, 500.0]),
                                       care_children=kids, care_earned=earn)
    for i in range(4):
        s = c.child_dependent_care_credit(agi[i], str(status[i]), int(deps[i]), care_children=kids[i], care_earned=earn[i])
        assert cc[i] == s
    assert cc[2] == 0.0 and 0 < cc[3] < cc[1]


def test_the_calculator_reads_the_unit_inputs_from_the_frame():
    f = pd.DataFrame({"filing_status": ["married_filing_jointly"] * 2, "earned_income": [100_000.0, 100_000.0],
                      "primary_wagp": [99_700.0, 60_000.0], "primary_semp": [0.0, 0.0],
                      "secondary_wagp": [300.0, 40_000.0], "secondary_semp": [0.0, 0.0],
                      "num_children_under_13": [2.0, np.nan]})
    kids, earn = care_credit_inputs(f)
    assert kids.tolist() == [2.0, 0.0] and earn.tolist() == [300.0, 40_000.0]
    assert care_credit_inputs(f.drop(columns=["num_children_under_13"]))[0] is None
    assert care_credit_inputs(f.drop(columns=["primary_wagp"]))[1] is None
    kids2, earn2 = care_credit_inputs(f.assign(care_children=[1, 1], care_earned=[5.0, 6.0]))
    assert kids2.tolist() == [1.0, 1.0] and earn2.tolist() == [5.0, 6.0]


def test_unit_liabilities_use_the_frame_inputs():
    from tax_modeler.config.tax_system_config import TaxSystemRegistry

    cfg = TaxSystemRegistry.get_act46_system(2027)
    f = pd.DataFrame({"income": [40_000.0] * 3, "filing_status": ["head_of_household"] * 3,
                      "num_dependents": [2, 2, 2], "weight": [1.0] * 3,
                      "care_children": [2, 0, 2], "care_earned": [np.inf, np.inf, 200.0]})
    cc = TaxCalculator().unit_liabilities(f, cfg)["child_care"]
    assert cc[1] == 0.0 and 0 < cc[2] < cc[0]


def test_the_kernel_tables_carry_the_limits_schedule_and_donors_by_year():
    t = food_excise_tables([2027, 2028])
    assert t["2027"]["cdcc"]["caps"] == [10_000.0, 20_000.0]
    assert t["2028"]["cdcc"]["caps"] == [2_400.0, 4_800.0]
    c = t["2028"]["cdcc"]
    assert c["percent"][0] == [25_000.0, 0.25] and c["percent_above"] == 0.15
    for b in ("1", "2"):
        x, p = DONORS[int(b)]
        assert c["donors"][b]["x"] == [float(v) for v in x] and c["donors"][b]["p"] == [float(v) for v in p]
