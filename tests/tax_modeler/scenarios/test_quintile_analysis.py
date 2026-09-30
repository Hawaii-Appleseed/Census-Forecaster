"""Quintile tables: fifths of households, totals that add up to the fiscal totals.

Regression for two defects in ``generate_quintile_report`` found in the
September 29, 2026 pipeline audit (the published Act 24 table reproduced
both to the cent):

1. The breaks were cut on the filer weight of each household's first tax
   unit, while the tables count households with the PUMS household weight
   (WGTP). Filer weights are raked unit by unit and differ within most
   multi-unit households, so the published "fifths" held 16-24% of
   households.
2. Each household's summed tax was multiplied by that one first-unit filer
   weight, so the quintile totals did not add up to the fiscal totals
   (sum over units of weight x tax): Act 46 in TY2027 came to $2,653M in the
   quintile table against $2,465M in the fiscal baseline.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemRegistry
from tax_modeler.projection.tax_unit_projector import refresh_stale_hawaii_tax
from tax_modeler.scenarios.quintile_analysis import (
    compute_quintile_breaks,
    generate_quintile_report,
    per_unit_tax,
)

YEAR = 2027
N_HH = 100
REPLACED_HH = "H005"          # holds a replaced $1M+ survey record (weight 0)
OVERLAY = {"reec_individual_savings_$M": 0.02, "reec_eligible_retained_share": 0.5,
           "cgec_individual_savings_$M": 0.005}
STATUSES = ["single", "married_filing_jointly", "head_of_household"]


def _frame() -> pd.DataFrame:
    """Households of one to three tax units, income rising with the index.

    Every unit of a household shares its WGTP; the filer weights differ
    within the household, and the first unit's grows with income, so a cut
    on the first filer weight crowds households into the bottom fifths.
    """
    rng = np.random.default_rng(7)
    rows = []
    for h in range(N_HH):
        hh_income = 8_000 * 1.06 ** h
        hw = float(rng.integers(5, 40))
        n_units = 1 + h % 3
        shares = rng.dirichlet(np.ones(n_units))
        for k in range(n_units):
            fw = 2 + 0.5 * h if k == 0 else float(rng.uniform(1, 60))
            income = hh_income * shares[k]
            rows.append({"hh_id": f"H{h:03d}", "hh_weight": hw, "weight": fw,
                         "income": income, "total_cash_income": income * 1.08,
                         "filing_status": STATUSES[(h + k) % 3], "num_dependents": (h + k) % 3})
    rows.append({"hh_id": REPLACED_HH, "hh_weight": rows[0]["hh_weight"], "weight": 0.0,
                 "income": 1_200_000.0, "total_cash_income": 1_250_000.0,
                 "filing_status": "single", "num_dependents": 0})
    df = pd.DataFrame(rows)
    df["hh_weight"] = df.groupby("hh_id")["hh_weight"].transform("first")
    return refresh_stale_hawaii_tax(df, target_year=YEAR)


@pytest.fixture(scope="module")
def calc():
    return TaxCalculator()


@pytest.fixture(scope="module")
def frame():
    return _frame()


@pytest.fixture(scope="module")
def systems():
    return TaxSystemRegistry.get_act46_system(YEAR), TaxSystemRegistry.get_sb3125_cd2_system(YEAR)


@pytest.fixture(scope="module")
def report(frame, systems, calc):
    base, reform = systems
    return generate_quintile_report(frame, base, reform, OVERLAY, calc, scenario_params={},
                                    quintile_breaks=compute_quintile_breaks(frame))


def test_the_frame_has_unequal_filer_weights_within_households(frame):
    g = frame.groupby("hh_id")
    assert (g["hh_weight"].nunique() == 1).all()
    assert (g["weight"].nunique() > 1).sum() > N_HH / 2


def test_each_fifth_holds_a_fifth_of_households(frame, report):
    q, _, _ = report
    kept = frame[frame["weight"] > 0.01].groupby("hh_id")["hh_weight"].first()
    total = kept.sum()
    assert q["household_count"].sum() == pytest.approx(total)
    # within one household's weight of a fifth (the breaks fall on households)
    granularity = kept.max() / total
    for share in q["household_count"] / total:
        assert abs(share - 0.2) < granularity


def test_quintile_totals_are_the_fiscal_totals(frame, systems, calc, report):
    q, b, _ = report
    base, reform = systems
    kept = frame["weight"].to_numpy(float) > 0.01
    w = frame["weight"].to_numpy(float)[kept]
    t46 = per_unit_tax(frame, base, calc)[kept]
    t24 = per_unit_tax(frame, reform, calc)[kept]
    assert q["total_act46_$M"].sum() == pytest.approx((w * t46).sum() / 1e6, rel=1e-9)
    assert q["total_cd1_$M"].sum() == pytest.approx((w * t24).sum() / 1e6, rel=1e-9)
    assert q["total_bracket_$M"].sum() == pytest.approx((w * (t24 - t46)).sum() / 1e6, rel=1e-9)
    credit = OVERLAY["reec_individual_savings_$M"] + OVERLAY["cgec_individual_savings_$M"]
    assert q["total_credit_loss_$M"].sum() == pytest.approx(credit, rel=1e-9)
    # ... and the income-class table's
    for col in ("total_act46_$M", "total_cd1_$M", "total_change_$M"):
        assert q[col].sum() == pytest.approx(b[col].sum(), rel=1e-9)


def test_per_household_averages_divide_the_totals_by_households(report):
    q, _, _ = report
    for x in ("act46_tax", "cd1_tax", "bracket_change", "credit_loss", "total_change"):
        total = {"act46_tax": "total_act46_$M", "cd1_tax": "total_cd1_$M",
                 "bracket_change": "total_bracket_$M", "credit_loss": "total_credit_loss_$M",
                 "total_change": "total_change_$M"}[x]
        np.testing.assert_allclose(q[f"avg_per_hh_{x}"] * q["household_count"], q[total] * 1e6,
                                   rtol=1e-9)
    claimants = q["household_count"] * q["pct_credit_claimant"] / 100
    np.testing.assert_allclose(q["avg_credit_loss_per_claimant"] * claimants,
                               q["total_credit_loss_$M"] * 1e6, rtol=1e-9)


def test_a_replaced_survey_record_does_not_move_its_household(frame, report):
    # Its $1.25M would put the household in the top fifth; the record is
    # left out of the tables, and so is its income.
    _, _, pu = report
    members = frame[frame["hh_id"] == REPLACED_HH]
    assert (members["weight"] == 0).sum() == 1 and len(members) > 1
    got = pu.loc[pu["hh_id"] == REPLACED_HH, "quintile"].astype(str)
    assert len(got) == len(members) - 1
    assert set(got) == {"Q1 (bottom 20%)"}


def test_floating_breaks_are_the_frames_own_fifths(frame, systems, calc, report):
    base, reform = systems
    q, _, _ = generate_quintile_report(frame, base, reform, OVERLAY, calc, scenario_params={})
    pd.testing.assert_frame_equal(q, report[0])
