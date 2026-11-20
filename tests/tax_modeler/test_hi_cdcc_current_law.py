"""The legacy credit calculator's current-law care credit is HRS §235-55.6 (via credits/hi_cdcc), by tax year,
and the browser kernel's tables carry the same limits and schedule."""
from __future__ import annotations

import numpy as np
import pytest
from tax_modeler.adjustments.hawaii_credits import (
    ASSUMED_CARE_EXPENSE_PER_DEPENDENT,
    HawaiiTaxCredits,
    apply_credits_to_dataframe,
    calculate_hawaii_credits,
)
from tax_modeler.credits import hi_cdcc
from tax_modeler.simulator.systems import food_excise_tables


@pytest.mark.parametrize("year", [2022, 2023, 2025, 2027, 2028, 2031])
@pytest.mark.parametrize("agi", [0, 20_000, 25_000, 25_001, 47_000, 50_000, 50_001, 120_000, 500_000])
@pytest.mark.parametrize("deps", [1, 2, 4])
def test_current_law_is_the_shared_statute(year, agi, deps):
    got = HawaiiTaxCredits(year).child_dependent_care_credit(agi, "single", deps)
    want = hi_cdcc.cdcc_credit(agi, deps, ASSUMED_CARE_EXPENSE_PER_DEPENDENT * deps, np.inf,
                               hi_cdcc.caps_for_year(year))
    assert got == float(want)


def test_the_convenience_function_and_the_dataframe_path_take_the_year():
    kw = dict(agi=20_000, filing_status="single", num_dependents=1, tax_before_credits=0)
    assert calculate_hawaii_credits(year=2027, **kw)["child_care"] == pytest.approx(0.25 * 10_000)
    assert calculate_hawaii_credits(year=2028, **kw)["child_care"] == pytest.approx(0.25 * 2_400)
    import pandas as pd
    df = pd.DataFrame({"agi": [20_000.0], "filing_status": ["single"], "num_dependents": [1], "tax_liability": [0.0]})
    assert apply_credits_to_dataframe(df, year=2027)["hi_credit_child_care"].iloc[0] == pytest.approx(2_500)
    assert apply_credits_to_dataframe(df, year=2028)["hi_credit_child_care"].iloc[0] == pytest.approx(600)


def test_the_credit_is_paid_when_there_is_no_tax():
    r = HawaiiTaxCredits(2027).calculate_total_credits(30_000, "head_of_household", 2, 0.0)
    assert r["child_care"] == pytest.approx(0.24 * 20_000)
    assert r["total_refundable"] >= r["child_care"] and r["total"] >= r["child_care"]


def test_bill_branches_are_as_the_bills_wrote_them():
    c = HawaiiTaxCredits(2027)
    assert c.child_dependent_care_credit(50_000, "head_of_household", 2, credit_scenario="hb2306_hd1") == pytest.approx(10_000)
    assert c.child_dependent_care_credit(43_000, "single", 1, credit_scenario="sb3125_sd1") == pytest.approx(3_500)


def test_the_kernel_tables_carry_the_limits_and_schedule_by_year():
    t = food_excise_tables([2027, 2028])
    assert t["2027"]["cdcc"]["caps"] == [10_000.0, 20_000.0]
    assert t["2028"]["cdcc"]["caps"] == [2_400.0, 4_800.0]
    assert t["2028"]["cdcc"]["percent"][0] == [25_000.0, 0.25] and t["2028"]["cdcc"]["percent_above"] == 0.15
    assert t["2028"]["cdcc"]["expense_per_dependent"] == ASSUMED_CARE_EXPENSE_PER_DEPENDENT
