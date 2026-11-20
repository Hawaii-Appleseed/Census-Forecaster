"""Count margins for families with children, by AGI class, from IRS SOI Table 2 for Hawaiʻi, scaled class by class to the
Department's resident universe, and raked jointly with DOTAX's margins."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration import apply_ipf_calibration_via_rake, dotax_base, irs_soi_hi_table2
from tax_modeler.calibration import family_margins as fm


def test_universe_ratios_are_department_over_irs_on_shared_edges():
    rows = fm.universe_ratios()
    assert len(rows) == 8
    assert sum(r["dotax"] for r in rows) == sum(dotax_base.filer_targets().values())
    assert sum(r["irs"] for r in rows) == pytest.approx(sum(irs_soi_hi_table2.values("returns", 2023)))
    for r in rows:
        assert r["ratio"] == pytest.approx(r["dotax"] / r["irs"])
    # not one ratio: more Department returns than IRS ones under $10,000, fewer in every class above $50,000
    assert rows[0]["ratio"] > 1.2 and all(r["ratio"] < 0.9 for r in rows[2:])
    assert rows[1]["ratio"] == pytest.approx(0.960, abs=1e-3)       # $10,000 to $50,000


def test_the_edges_the_two_tables_share_are_the_aligned_edges():
    dot_edges = {float(lo) for lo, _ in dotax_base.filer_targets()}
    irs_edges = {lo for lo, _ in irs_soi_hi_table2.bands() if np.isfinite(lo)}
    for e in fm.ALIGNED_EDGES[1:]:
        assert e in dot_edges and e in irs_edges


def test_margin_targets_are_irs_counts_times_the_class_ratio():
    ratio = {i: r["ratio"] for i, r in enumerate(fm.universe_ratios())}
    t = fm.margin_targets("hoh_returns")
    # classes: under $10k, $10-50k, $50-75k, $75-100k, $100k-$1M
    assert t[0] == pytest.approx((650 + 4_110) * ratio[0])
    assert t[1] == pytest.approx((11_380 + 24_020) * ratio[1])
    assert t[2] == pytest.approx(15_890 * ratio[2]) and t[3] == pytest.approx(7_860 * ratio[3])
    assert t[4] == pytest.approx(7_570 * ratio[4] + 1_130 * ratio[5] + 130 * ratio[6])     # $1M+ is left to the tail
    a = fm.margin_targets("actc_returns")
    assert a[3] == pytest.approx(2_040 * ratio[3] + 1_470 * ratio[4] + 170 * ratio[5])
    c = fm.margin_targets("ctc_nonref_returns")
    assert c[0] == pytest.approx(90 * ratio[0] + (4_800 + 34_650) * ratio[1])
    # head of household returns land near the Department's own head-of-household count
    assert sum(t.values()) == pytest.approx(dotax_base.status_targets()["head_of_household"], rel=0.05)


def test_other_dependent_credit_counts_dependents_who_are_not_ctc_children():
    u = pd.DataFrame({"dependents_details": [[{"age": 5}, {"age": 17}, {"age": 30}], [{"age": 4}], [], None]})
    assert fm.odc_amount(u).tolist() == [1_000.0, 0.0, 0.0, 0.0]


def _units(n=400, seed=2):
    rng = np.random.default_rng(seed)
    agi = np.exp(rng.normal(10.8, 0.9, n))
    status = rng.choice(["single", "married_filing_jointly", "head_of_household", "married_filing_separately"], n)
    kids = rng.integers(0, 3, n)
    return pd.DataFrame({
        "agi": agi, "federal_agi": agi, "filing_status": status, "weight": rng.uniform(60, 140, n),
        "hi_state_tax": np.maximum(agi - 20_000, 0) * 0.05,
        "ctc_nonrefundable": np.where(kids > 0, 1_500.0, 0.0), "ctc_refundable": np.where((kids > 0) & (agi < 50_000), 900.0, 0.0),
        "federal_tax_before_credits": agi * 0.1,
        "dependents_details": [[{"age": 6}] * int(k) for k in kids]})


def test_flags_follow_the_models_own_credits_and_the_other_dependent_credit():
    u = _units(6)
    u["dependents_details"] = [[{"age": 6}], [], [{"age": 19}], [{"age": 19}], [], []]
    u["ctc_nonrefundable"] = [1_500.0, 0, 0, 0, 0, 0]
    u["federal_tax_before_credits"] = [5_000.0, 5_000.0, 5_000.0, 0.0, 5_000.0, 5_000.0]
    u["ctc_refundable"] = [0.0, 0, 800.0, 0, 0, 0]
    u["filing_status"] = ["head_of_household"] + ["single"] * 5
    f = fm.family_flags(u)
    assert f["ctc_nonref_returns"].tolist() == [True, False, True, False, False, False]    # unit 3: no tax left to use it
    assert f["actc_returns"].tolist() == [False, False, True, False, False, False]
    assert f["hoh_returns"].tolist() == [True] + [False] * 5


def test_codes_put_flagged_units_in_their_class_and_everyone_else_on_the_remainder():
    u = _units()
    codes = fm.margin_codes(u)
    assert set(codes) == set(fm.MARGIN_EDGES)
    total = float(sum(dotax_base.status_targets().values()))
    for key, (c, t) in codes.items():
        assert set(np.unique(c)) <= set(range(len(fm.MARGIN_EDGES[key]) + 1))
        assert sum(t.values()) == pytest.approx(total)
        assert t["0"] > 0.7 * total
    assert (codes["hoh_returns"][0][u["filing_status"] != "head_of_household"] == 0).all()


def test_a_frame_without_the_federal_credit_columns_has_no_margins():
    assert fm.margin_codes(_units().drop(columns=["ctc_refundable"])) is None


def test_the_rake_with_the_margins_moves_toward_them_and_keeps_the_total():
    u = _units(3000, seed=4)
    off = apply_ipf_calibration_via_rake(u, calibrate_families=False)
    on = apply_ipf_calibration_via_rake(u, calibrate_families=True)
    codes_on = fm.margin_codes(on)
    codes_off = fm.margin_codes(off)

    def err(codes, w):
        e = []
        for c, t in codes.values():
            for k, v in t.items():
                if k != "0":
                    e.append(abs(w[c == int(k)].sum() / v - 1))
        return np.mean(e)

    assert err(codes_on, on["weight"].to_numpy()) < err(codes_off, off["weight"].to_numpy())
    assert on["weight"].sum() == pytest.approx(off["weight"].sum(), rel=0.03)
    assert (on["weight"] > 0).all() and not any(c.startswith("_x_") for c in on.columns)


def test_without_the_option_or_the_columns_the_rake_is_the_old_one():
    u = _units(1500)
    bare = u.drop(columns=["ctc_refundable"])
    a = apply_ipf_calibration_via_rake(bare, calibrate_families=True)
    b = apply_ipf_calibration_via_rake(bare, calibrate_families=False)
    np.testing.assert_array_equal(a["weight"].to_numpy(), b["weight"].to_numpy())
