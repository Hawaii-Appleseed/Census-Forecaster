"""Hawaiʻi SNAP rules by fiscal year, and placing SNAP on the population (benefits/snap.py)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.benefits.snap import (
    SNAP_FISCAL_YEARS,
    _compute_household_snap,
    calibrate_snap_receipt,
    hawaii_snap_parameters,
    qc_average_benefit_by_size,
)
from tax_modeler.calibration.admin_caseload import AdminCaseload


@pytest.mark.parametrize("fy, one, four, gross4, std1, minimum, net_test", [
    (2024, 527, 1759, 5750, 279, 42, True),
    (2025, 517, 1723, 5980, 287, 41, False),
    (2026, 506, 1689, 6164, 295, 41, False),
    (2027, 496, 1655, 6326, 306, 40, False),
])
def test_tables_match_usda(fy, one, four, gross4, std1, minimum, net_test):
    p = hawaii_snap_parameters(fy)
    assert p.fiscal_year == fy
    assert p.max_benefit_by_size[1] == one and p.max_benefit_by_size[4] == four
    assert p.gross_income_limit_by_size[4] == gross4          # 200 percent of poverty
    assert p.standard_deduction_by_size[1] == std1
    assert p.min_benefit == minimum and p.net_income_test is net_test


def test_years_outside_the_tables_use_the_nearest():
    assert SNAP_FISCAL_YEARS == (2024, 2025, 2026, 2027)
    assert hawaii_snap_parameters(2031).fiscal_year == 2027
    assert hawaii_snap_parameters(2022).fiscal_year == 2024
    assert hawaii_snap_parameters().fiscal_year == 2027


def test_household_formula():
    p = hawaii_snap_parameters(2027)
    assert _compute_household_snap(0.0, 0.0, 1, p) == 496
    assert _compute_household_snap(1_000.0, 1_000.0, 2, p) == pytest.approx(910 - 0.3 * (1_000 - 306 - 200))
    assert _compute_household_snap(3_100.0, 0.0, 1, p) == 0.0          # over the gross limit
    assert _compute_household_snap(2_000.0, 0.0, 1, p) == 40.0         # the minimum
    # FY2024 still had the net test: a household of one with $1,700 net income was out.
    assert _compute_household_snap(2_000.0, 0.0, 1, hawaii_snap_parameters(2024)) == 0.0


def test_qc_average_restates_to_the_year():
    a24, a27 = qc_average_benefit_by_size(2024), qc_average_benefit_by_size(2027)
    assert set(a24) == {1, 2, 3, 4, 5, 6}
    assert a27[1] == pytest.approx(a24[1] * 496 / 527)


def _units():
    return pd.DataFrame({
        "hh_id": ["a", "b", "b", "c", "d", "e"],
        "filing_status": ["single"] * 6,
        "num_dependents": [0] * 6,
        "income": [0.0, 6_000.0, 0.0, 120_000.0, 6_000.0, 3_000.0],
        "earned_income": [0.0, 6_000.0, 0.0, 120_000.0, 6_000.0, 0.0],
        "weight": [1.0] * 6,
        "hh_weight": [100.0, 100.0, 100.0, 100.0, 100.0, 100.0],
        "hh_n_persons": [1, 2, 2, 1, 1, 1],
        "hh_snap_reported": [True, True, True, True, False, False],
    })


def test_receipt_keeps_reporters_draws_the_rest_and_hits_the_totals():
    out, r = calibrate_snap_receipt(_units(), target_households=500, target_dollars=1e6,
                                    fallback_benefit_by_size={n: 100.0 for n in range(1, 7)})
    hh = r.households.set_index("hh_id")
    assert hh.loc[["a", "b", "c"], "recipient"].all()
    assert hh.loc["c", "snap"] == 100.0                         # reports SNAP, over the limit: size average
    assert r.reported == 300 and r.reported_ineligible == 100
    assert r.draw_rate == pytest.approx(1.0)                     # 200 short, 200 eligible non-reporters
    assert r.imputed == 200
    w = hh["weight"]
    annual = out.groupby("hh_id")["snap_amount"].sum()
    assert (annual * w).sum() == pytest.approx(1e6)
    # A two-unit household's benefit is split between its units.
    assert out.loc[out["hh_id"] == "b", "snap_amount"].gt(0).all()


def test_receipt_without_survey_answers_draws_everyone():
    u = _units().drop(columns="hh_snap_reported")
    out, r = calibrate_snap_receipt(u, target_households=200, target_dollars=1e5,
                                    fallback_benefit_by_size={n: 100.0 for n in range(1, 7)})
    assert r.reported == 0
    assert 0 < r.draw_rate < 1
    assert np.isclose(r.households.loc[r.households["recipient"], "weight"].sum() > 0, True)


def test_caseload_latest_year_on_or_before():
    c = AdminCaseload.load()
    assert c.latest("snap", 2028).year == 2027
    assert c.latest("snap", 2025).year == 2025
    assert c.latest("snap", 2019).year == 2022
    t = c.target("snap", 2027)
    assert t.count == 77_985 and t.annual_dollars_millions == pytest.approx(607.4)
