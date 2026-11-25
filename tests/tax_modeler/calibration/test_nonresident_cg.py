"""``calibration.nonresident_cg``: nonresidents' gains scored on returns built
from DOTAX Table 21 and A-3, checked against A-3's tax."""
from __future__ import annotations

import numpy as np
import pytest
from tax_modeler.calibration import dotax_base, nonresident_cg
from tax_modeler.config.tax_system_config import TaxSystemRegistry as R


@pytest.mark.parametrize("year", nonresident_cg.YEARS)
def test_a3_classes_sum_to_printed_totals(year):
    a3 = dotax_base.load_indinc(year)["table_a3_nonresident_by_hawaii_agi"]
    rows, comp, tot = a3["taxable_classes"], a3["taxable_composite"], a3["total_taxable"]
    assert sum(r["returns"] for r in rows) + comp["returns"] == tot["returns"]
    assert abs(sum(r["tax_before_M"] for r in rows) + comp["tax_before_M"] - tot["tax_before_M"]) <= 6


@pytest.mark.parametrize("year", nonresident_cg.YEARS)
@pytest.mark.parametrize("placement", nonresident_cg.GAIN_PLACEMENTS)
def test_records_hold_a3_returns_income_and_table_21_gains(year, placement):
    rec = nonresident_cg.records(year, placement)
    ed = nonresident_cg.edition(year)
    w = rec["weight"].to_numpy()
    assert w.sum() == pytest.approx(ed["returns"].sum())
    top = (rec["agi_lo"] == 400_000).to_numpy()
    assert (w * rec["ti"])[top].sum() / 1e6 == pytest.approx(ed["ti_M"].iloc[-1], rel=1e-9)
    assert (w * rec["gains"])[top].sum() / 1e6 == pytest.approx(ed["gains_M"].iloc[-1], rel=1e-9)
    assert (rec["gains"] <= rec["ti"] + 1e-6).all()


@pytest.mark.parametrize("year", nonresident_cg.YEARS)
@pytest.mark.parametrize("placement", nonresident_cg.GAIN_PLACEMENTS)
def test_base_year_tax_reproduces_a3(year, placement):
    c = nonresident_cg.check_a3(year, placement)
    # A-3 prints tax to $1M; the two lowest classes are a few $M and lose the most to rounding
    assert c["model_tax_M"].sum() / c["tax_before_M"].sum() == pytest.approx(1.0, abs=0.03)
    top = c.iloc[-1]                                                     # $400K+: 3%, plus A-3's rounding
    assert abs(top["model_tax_M"] - top["tax_before_M"]) <= 0.03 * top["tax_before_M"] + 0.5
    assert c["ratio"].iloc[2:].between(0.9, 1.1).all()


def test_levels_bracket_ty2023():
    f = {lv: nonresident_cg.level_factor(lv) for lv in nonresident_cg.LEVEL_YEARS}
    assert f["central"] == 1.0
    assert f["low"] == pytest.approx(0.783, abs=0.002)
    assert f["high"] == pytest.approx(1.445, abs=0.002)


def test_composites_are_left_out():
    total = dotax_base.load_indinc(2022)["table_21_nltcg"]["total"]["nonres_amount_M"]
    assert nonresident_cg.nonresident_gains_M(2022) == pytest.approx(total - 208.3, abs=1)


def test_current_cap_raises_nothing_and_gains_only_raises_less():
    cfg = R.get_sb3125_cd2_system(2027)
    assert nonresident_cg.score(cfg, 2027, nonresident_cg.CAP_CURRENT)["static_M"] == pytest.approx(0, abs=1e-6)
    central = nonresident_cg.score(cfg, 2027, None)
    low = nonresident_cg.score(cfg, 2027, None, placement="gains_only")
    assert 0 < low["static_M"] < central["static_M"]
    assert central["gains_M"] == pytest.approx(low["gains_M"])


def test_offset_sellers_do_not_respond():
    cfg = R.get_sb3125_cd2_system(2027)
    s = {o: nonresident_cg.score(cfg, 2027, None, beta=2.0, offset_share=o) for o in (0.0, 0.5, 1.0)}
    assert s[1.0]["behavioral_M"] == pytest.approx(s[1.0]["static_M"])
    assert s[0.0]["behavioral_M"] < s[0.5]["behavioral_M"] < s[1.0]["behavioral_M"]
    assert np.isclose(s[0.5]["static_M"], s[0.0]["static_M"])


def test_home_state_credit_spares_about_two_fifths():
    o = nonresident_cg.seller_origin()
    assert {"HI", "CA", "unknown"} <= set(o["origin"])
    # California's 13.3% covers any rise up to 13%; New York's 9.65% covers a 9% cap only
    for rate in (0.09, 0.13):
        assert 0.35 < nonresident_cg.home_state_offset(rate) < 0.45
    assert nonresident_cg.home_state_offset(0.09) > nonresident_cg.home_state_offset(0.13)
