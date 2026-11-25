"""tax_modeler.excise.get_incidence: spending read from income, scaled to a total, taxed; and the
fetch script's parser for the Department of Taxation's sheet."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import numpy as np
import pytest
from tax_modeler.errors import ConfigError
from tax_modeler.excise import get_incidence as gi

REPO = Path(__file__).resolve().parents[3]
ITEMS = sorted({i for c in gi.CATEGORIES for i in c.cex_items} | {"TOTALEXP"})


def _profile() -> gi.SpendingProfile:
    """Ten deciles, every item $1,000 at the lowest and rising with elasticity 0.5."""
    income = np.geomspace(10_000, 320_000, 10)
    return gi.SpendingProfile(income=income, items={i: 1_000 * (income / income[0]) ** 0.5 for i in ITEMS})


def _households(n: int = 400):
    rng = np.random.default_rng(7)
    income = rng.lognormal(11.3, 0.9, n)
    weight = rng.uniform(50, 150, n)
    rent = np.where(rng.random(n) < 0.4, 24_000.0, 0.0)
    return income, weight, rent


def test_profile_passes_through_the_decile_means_and_extends_past_them():
    p = _profile()
    np.testing.assert_allclose(p.at(("FOODHOME",), p.income), p.items["FOODHOME"])
    np.testing.assert_allclose(p.at(("FOODHOME", "FOODAWAY"), p.income[3:4]), 2 * p.items["FOODHOME"][3])
    # below the lowest decile's mean (and at zero or negative income) spending stays at that decile's
    np.testing.assert_allclose(p.at(("FOODHOME",), np.array([-5_000.0, 0.0, 2_000.0])), 1_000.0)
    # above the highest it keeps the last interval's elasticity
    np.testing.assert_allclose(p.at(("FOODHOME",), np.array([1_280_000.0])), 2 * p.items["FOODHOME"][-1])
    with pytest.raises(ConfigError):
        p.at(("NOSUCH",), p.income)


def test_spending_is_scaled_to_the_given_totals_and_rent_is_kept():
    income, weight, rent = _households()
    pce = {line: 1e6 * (line + 1) for c in gi.CATEGORIES for line in (*c.pce_lines, *c.goods_lines)}
    s = gi.impute_spending(income, weight, rent, _profile(), pce=pce)
    for c in gi.CATEGORIES:
        total = float((s.amounts[c.key] * weight).sum())
        if c.pce_lines:
            assert total == pytest.approx(sum(pce[i] for i in c.pce_lines))
        else:
            assert s.scale[c.key] == 1.0
    np.testing.assert_array_equal(s.amounts[gi.RENT], rent)
    survey = gi.impute_spending(income, weight, rent, _profile())
    assert survey.scale["groceries"] == 1.0
    assert survey.amounts["telecom"].sum() == 0      # no survey item of its own
    # a category rising more slowly than income takes a larger share of a low income
    share = s.amounts["groceries"] / income
    assert share[np.argmin(income)] > share[np.argmax(income)]


def test_tax_is_the_rate_on_what_was_paid_with_snap_groceries_exempt():
    income, weight, rent = np.array([50_000.0, 50_000.0]), np.array([1.0, 1.0]), np.array([0.0, 12_000.0])
    s = gi.impute_spending(income, weight, rent, _profile())
    groceries = s.amounts["groceries"].to_numpy()
    snap = np.array([0.0, groceries[1] + 500])       # more SNAP than groceries: none of them taxed
    law = gi.Law(wholesale=0.0025)
    pce = {i: 1.0 for c in gi.CATEGORIES for i in (*c.pce_lines, *c.goods_lines)}
    t = gi.burden(s, law, snap=snap, pce=pce)
    assert t["state_groceries"][0] == pytest.approx(groceries[0] * (0.04 + 0.0025))
    assert t["county_groceries"][0] == pytest.approx(groceries[0] * 0.005)
    assert t["state_groceries"][1] == pytest.approx(groceries[1] * 0.0025)     # the wholesale levy stays
    assert t["county_groceries"][1] == 0
    assert t["state_rent"][1] == pytest.approx(12_000 * 0.04) and t["state_rent"][0] == 0
    services = s.amounts["vehicle_services"].to_numpy()
    assert t["state_vehicle_services"][0] == pytest.approx(services[0] * 0.04)   # a service: no wholesale levy
    np.testing.assert_allclose(t["total"], t["state"] + t["county"])
    np.testing.assert_allclose(t["state"], sum(t[f"state_{k}"] for k in gi.CATEGORY_KEYS))


def test_an_exemption_removes_the_retail_tax_only():
    income, weight, rent = _households()
    s = gi.impute_spending(income, weight, rent, _profile())
    pce = {i: 1.0 for c in gi.CATEGORIES for i in (*c.pce_lines, *c.goods_lines)}
    law = gi.Law(wholesale=0.0025, rent_pass_through=0.5)
    before = gi.burden(s, law, pce=pce)
    after = gi.burden(s, gi.Law(wholesale=0.0025, rent_pass_through=0.5, exempt=frozenset({"groceries", "rent"})), pce=pce)
    np.testing.assert_allclose(before["state_rent"], rent * 0.04 * 0.5)
    np.testing.assert_allclose(after["state_groceries"], s.amounts["groceries"] * 0.0025)
    assert (after["state_rent"] == 0).all() and (after["county_groceries"] == 0).all()
    np.testing.assert_allclose(after["state_apparel"], before["state_apparel"])
    with pytest.raises(ConfigError):
        gi.Law(exempt=frozenset({"yachts"}))


def test_goods_share_is_the_goods_lines_over_all_lines():
    recreation = next(c for c in gi.CATEGORIES if c.key == "recreation")
    pce = {i: 10.0 for i in recreation.pce_lines} | {i: 5.0 for i in recreation.goods_lines if i not in recreation.pce_lines}
    expected = sum(pce[i] for i in recreation.goods_lines) / sum(pce[i] for i in recreation.pce_lines)
    assert gi.goods_share(recreation, pce) == pytest.approx(expected)
    assert gi.goods_share(next(c for c in gi.CATEGORIES if c.key == "groceries")) == 1.0
    assert gi.goods_share(next(c for c in gi.CATEGORIES if c.key == "vehicle_services")) == 0.0
    with pytest.raises(ConfigError):
        gi.goods_share(recreation)


def test_income_groups_hold_equal_weight_in_income_order():
    income, weight, _ = _households(2_000)
    g = gi.income_groups(income, weight, 5)
    shares = np.bincount(g, weights=weight) / weight.sum()
    np.testing.assert_allclose(shares, 0.2, atol=0.002)
    assert all(income[g == i].max() <= income[g == i + 1].min() for i in range(4))


def test_the_bundled_collections_add_up_and_feed_the_wholesale_markup():
    d = gi.dotax_get(2025)
    parts = sum(v for k, v in d["tax"].items()
                if k not in ("allocated collections", "unallocated net collections", "grand total"))
    assert parts == pytest.approx(d["tax"]["allocated collections"], rel=1e-9)
    assert d["tax"]["retailing"] == pytest.approx(0.04 * d["base"]["retailing"], rel=1e-6)
    assert gi.wholesale_markup(2025) == pytest.approx(d["tax"]["wholesaling"] / d["base"]["retailing"])
    assert 0.002 < gi.wholesale_markup(2025) < 0.004
    with pytest.raises(ConfigError):
        gi.dotax_get(1999)


def test_every_category_line_is_in_the_bundled_bea_file():
    pce = gi.pce_hawaii(2024)
    for c in gi.CATEGORIES:
        assert set(c.pce_lines) | set(c.goods_lines) <= set(pce), c.key
        assert 0.0 <= gi.goods_share(c, pce) <= 1.0, c.key
    assert pce[4] == pytest.approx(6_931.4e6)      # food and nonalcoholic beverages for off-premises consumption


def test_control_totals_put_the_modelled_tax_beside_collections():
    income, weight, rent = _households()
    s = gi.impute_spending(income, weight, rent, _profile(), pce=gi.pce_hawaii(2024))
    t = gi.burden(s, gi.Law(wholesale=gi.wholesale_markup(2025)), pce=gi.pce_hawaii(2024))
    c = gi.control_totals(t, weight, 2025).set_index("line")["dollars"]
    modelled = c["Modelled: resident households, state tax"]
    assert modelled == pytest.approx(float((t["state"] * weight).sum()))
    assert c["Not modelled (collections less modelled)"] == pytest.approx(
        gi.dotax_get(2025)["tax"]["allocated collections"] - modelled)


def test_the_fetch_script_parses_both_blocks_of_the_departments_sheet():
    spec = importlib.util.spec_from_file_location("fetch_get_incidence_inputs",
                                                  REPO / "scripts" / "fetch_get_incidence_inputs.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    rows = (
        ["STATE OF HAWAII"],
        ["DEPARTMENT OF TAXATION", "GENERAL EXCISE AND USE TAX COLLECTIONS - JUNE 2026"],
        [None, "ALL DISTRICTS", None, "CUMULATIVE TOTALS"],
        [None, "JUN 2026", "JUN 2025", "2025-2026", "2024-2025"],
        ["Retailing", 1.0, 2.0, 40.0, 36.0],
        ["   SUB-TOTAL", 1.0, 2.0, 40.0, 36.0],
        ["Wholesaling", 1.0, 2.0, 5.0, 4.0],
        ["   SUB-TOTAL", 1.0, 2.0, 5.0, 4.0],
        ["   ALLOCATED COLLECTIONS", 2.0, 4.0, 45.0, 40.0],
        [None, "GENERAL EXCISE AND USE TAX BASE - JUNE 2026"],
        [None, "ALL DISTRICTS", None, "CUMULATIVE TOTALS"],
        [None, "JUN 2026", "JUN 2025", "2025-2026", "2024-2025"],
        ["Retailing", 25.0, 50.0, 1000.0, 900.0],
        ["NOTES:"],
    )

    class Sheet:
        def iter_rows(self, values_only):
            return iter(tuple(r) for r in rows)

    class Workbook:        # as much of openpyxl's as the parser reads
        sheetnames = ["Jun2026"]

        def __getitem__(self, name):
            return Sheet()

    got = mod.parse_dotax(Workbook())
    assert got == {"2026": {"tax": {"retailing": 40.0, "wholesaling": 5.0, "allocated collections": 45.0},
                            "base": {"retailing": 1000.0}},
                   "2025": {"tax": {"retailing": 36.0, "wholesaling": 4.0, "allocated collections": 40.0},
                            "base": {"retailing": 900.0}}}
