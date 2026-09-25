"""Act 163 (2023) credit schedules and their December 31, 2027 sunset.

HRS §235-55.75(a) and §235-55.85(b) are "[Repeal and reenactment on
December 31, 2027. L 2023, c 163, §5.]": 40% EITC and the doubled food/excise
table for TY2023-2027, then the pre-Act-163 text again from TY2028.
"""
from __future__ import annotations

import pandas as pd
import pytest

from tax_modeler.credits.hi_eitc import hawaii_eitc_parameters
from tax_modeler.credits.hi_food_excise import compute_hi_food_excise_for_units


@pytest.mark.parametrize("year,rate,refundable", [
    (2022, 0.20, False),   # Act 107 (2017)
    (2023, 0.40, True),    # Act 114 (2022) refundable + Act 163 (2023) 40%
    (2027, 0.40, True),
    (2028, 0.20, True),    # Act 163 repealed; Act 114 text reenacted
    (2031, 0.20, True),
])
def test_hi_eitc_rate_by_year(year, rate, refundable):
    p = hawaii_eitc_parameters(year)
    assert (p.rate_of_federal, p.refundable) == (rate, refundable)


def _unit(income, status="single", deps=0):
    return pd.DataFrame({"income": [income], "filing_status": [status], "num_dependents": [deps]})


@pytest.mark.parametrize("income,status,deps,act163,prior", [
    (4_000, "single", 0, 220, 110),
    (14_999, "single", 0, 220, 85),
    (15_000, "single", 0, 200, 70),        # "$15,000 under $20,000"
    (29_999, "single", 0, 140, 55),
    (35_000, "single", 0, 110, 0),         # prior law ends at $30,000 for singles
    (40_000, "single", 0, 0, 0),
    (35_000, "head_of_household", 1, 2 * 110, 2 * 45),
    (46_000, "married_filing_jointly", 1, 3 * 90, 3 * 35),
    (55_000, "married_filing_jointly", 2, 4 * 70, 0),
    (60_000, "married_filing_jointly", 2, 0, 0),
])
def test_food_excise_statutory_tables(income, status, deps, act163, prior):
    u = _unit(income, status, deps)
    assert compute_hi_food_excise_for_units(u, tax_year=2025)["hi_food_excise_amount"].iloc[0] == act163
    assert compute_hi_food_excise_for_units(u, tax_year=2028)["hi_food_excise_amount"].iloc[0] == prior
    assert compute_hi_food_excise_for_units(u, tax_year=2022)["hi_food_excise_amount"].iloc[0] == prior
