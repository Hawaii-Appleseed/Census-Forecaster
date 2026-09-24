"""Credit-cut losses land on imputed claimants, not on every filer in a class.

Regression for the Act 24 distribution tables (SB3125_CD1_FORECAST.md,
"CD2 rerun for the estimates site — September 24, 2026"): spreading each AGI
class's REEC loss over all its filers made every household under $10K
"pay more".
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tax_modeler.scenarios.quintile_analysis import (
    _change_shares,
    _claim_profile,
    attribute_credit_loss,
)
from tax_modeler.scenarios.sb3125_cd1_credits import (
    REEC_SUNSET_LAST_VINTAGE,
    compute_credit_overlay,
)


def _units() -> pd.DataFrame:
    # One unit per AGI class, plus an above-threshold joint and single filer.
    return pd.DataFrame({
        "income": [5e3, 20e3, 45e3, 80e3, 150e3, 300e3, 400e3, 190e3],
        "weight": [1000.0] * 8,
        "filing_status": ["single"] * 5 + ["married_filing_jointly"] * 2 + ["single"],
    })


def test_claim_profile_matches_dotax_ty2023():
    rate, avg = _claim_profile("reec")
    # A-6 / Table 2, TY2023: 521 / 136,725 and 2,509 / 69,981
    assert rate[0] == pytest.approx(521 / 136_725)
    assert rate[-1] == pytest.approx(2_509 / 69_981)
    assert (rate < 0.05).all()                       # most filers claim nothing
    assert avg[-1] == pytest.approx(26_017_902 / 2_509, rel=1e-6)


def test_losses_sum_to_the_individual_savings():
    u = _units()
    loss, q = attribute_credit_loss(u, reec_individual_m=0.5, reec_retained_share=0.6,
                                    cgec_individual_m=0.2)
    assert (loss * u["weight"]).sum() / 1e6 == pytest.approx(0.7)
    assert ((q > 0) & (q < 0.06)).all()


def test_only_agi_ineligible_claimants_lose_when_the_cap_does_not_bind():
    u = _units()
    loss, q = attribute_credit_loss(u, reec_individual_m=1.0, reec_retained_share=1.0)
    ineligible = np.array([False] * 6 + [True, True])   # $400K joint, $190K single
    assert (loss[~ineligible] == 0).all() and (q[~ineligible] == 0).all()
    assert (loss[ineligible] > 0).all()


def test_after_sunset_every_claimant_loses():
    loss, _ = attribute_credit_loss(_units(), reec_individual_m=1.0, reec_retained_share=0.0)
    assert (loss > 0).all()


def test_non_claimants_are_not_counted_as_paying_more():
    # No bracket change, 2% chance of claiming: 2% pay more, not 100%.
    more, less, same = _change_shares(np.zeros(1), np.array([40.0]), np.array([0.02]), np.ones(1))
    assert more == pytest.approx(2.0)
    assert same == pytest.approx(98.0) and less == 0


def test_a_claimants_loss_can_outweigh_a_bracket_cut():
    # Bracket cut of $80; a claimant's loss of $2,000 (expected $40 at q=0.02).
    more, less, _ = _change_shares(np.array([-80.0]), np.array([40.0]), np.array([0.02]), np.ones(1))
    assert more == pytest.approx(2.0) and less == pytest.approx(98.0)


@pytest.mark.parametrize("year", [2028, REEC_SUNSET_LAST_VINTAGE + 1])
def test_cd2_overlay_reports_the_individual_pool(year):
    ov = compute_credit_overlay(year, model_carryforward_pool=True, _ci_pass=True)
    assert 0 < ov["reec_individual_savings_$M"] <= ov["reec_savings_$M"] + 0.01
    assert 0 <= ov["reec_eligible_retained_share"] <= 1
    if year > REEC_SUNSET_LAST_VINTAGE:
        assert ov["reec_eligible_retained_share"] == 0
    assert ov["cgec_individual_savings_$M"] <= ov["cgec_savings_$M"]
