"""forecast_conveyance_sb3028.py: the statewide housing-stock method."""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("forecast_conveyance_sb3028", REPO / "forecast_conveyance_sb3028.py")
fc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fc
spec.loader.exec_module(fc)

pytestmark = pytest.mark.smoke


def test_maui_rates_give_back_maui_sales_on_maui_homes():
    # The rates are Maui's sales per home; applied to Maui's own homes they must
    # return Maui's (arm's-length, improved) sales, by buyer type.
    syn = fc.stock_sales(fc.county_stock("Maui"), fc.maui_rates())
    sv = pd.read_csv(fc.DATA / "maui_sales_by_value_fy2023_2026.csv").query("improved == 1")
    years = sv["fy"].nunique()
    for cat in ("owner", "nonowner"):
        assert syn.loc[syn["category"] == cat, "count"].sum() == pytest.approx(
            sv.loc[sv["category"] == cat, "count"].sum() / years, rel=0.01)


def test_price_spread_keeps_each_cell_count_and_mean():
    stock, rates = fc.county_stock("Maui"), fc.maui_rates()
    flat, spread = fc.stock_sales(stock, rates, 0.0), fc.stock_sales(stock, rates, fc.PRICE_SIGMA)
    assert len(spread) == len(flat) * fc.N_QUANTILES
    assert spread["count"].sum() == pytest.approx(flat["count"].sum())
    assert (spread["count"] * spread["price"]).sum() == pytest.approx((flat["count"] * flat["price"]).sum())


def test_price_spread_is_what_maui_shows():
    # Sigma is fitted so Maui's synthetic sales match its sales by price band.
    assert fc.fit_price_sigma() == pytest.approx(fc.PRICE_SIGMA, abs=0.05)


def test_apartment_building_sales_leave_the_price_bins():
    bins = pd.read_csv(fc.DATA / "maui_sales_bins_fy2023_2026.csv")
    tops = pd.read_csv(fc.DATA / "maui_sales_over_10m_fy2023_2026.csv")
    mf = pd.read_csv(fc.DATA / "maui_multifamily_sales_fy2023_2026.csv")
    s = fc.load_sales()
    assert (s["category"] == "mf").sum() == len(mf)
    assert s["count"].sum() == pytest.approx(bins["count"].sum() + len(tops))           # moved, not added
    assert (s["price"] * s["count"]).sum() == pytest.approx(bins["sum_price"].sum() + tops["price"].sum())


def test_per_unit_rule_cuts_the_tax_on_an_apartment_building():
    # $30M for 100 units: today 1.0% of the whole price (schedule 1, $10M+);
    # under HD2 the rate is set by $300,000 a unit, 0.15% (non-owner, under $600,000).
    one = pd.DataFrame({"fy": [2028], "category": ["mf"], "count": [1], "price": [30e6], "units": [100]})
    sc = fc.score(one, 2028)
    assert sc["tax_now"].iloc[0] == pytest.approx(300_000)
    assert sc["tax_hd2"].iloc[0] == pytest.approx(45_000)


def test_band_weights_return_maui_its_own_recorded_sales():
    sales = fc.load_sales()
    rec = fc.by_band(fc.maui_homes(sales))
    syn = fc.stock_sales(fc.county_stock("Maui"), fc.maui_rates())
    w = fc.band_weights(syn, None, syn, rec)
    np.testing.assert_allclose(fc.by_band(fc.weighted(syn, w)), rec)


def test_counties_are_calibrated_to_their_mls_sales_by_band():
    sm = fc.stock_method(fc.SALES_BASE_FY)
    rec = fc.by_band(fc.maui_homes(fc.load_sales()))
    mls = fc.mls_targets()
    for c in fc.STOCK_COUNTIES:
        got = fc.by_band(sm["calibrated"][c])
        for j in mls.columns:                               # from $1M: MLS x Maui's recorded/MLS ratio
            assert got[j] == pytest.approx(mls.loc[c, j] * rec[j] / mls.loc["Maui", j])
    assert "Kauai" in fc.STOCK_COUNTIES and sm["Kauai"] > 0
    assert sm["residual"] == pytest.approx(1.0, abs=0.1)    # calibration leaves little for the residual


def test_entity_sales_escape_only_the_new_tax_on_top_non_owner_sales():
    rows = pd.DataFrame({"fy": 2028, "category": ["nonowner", "nonowner", "owner"], "count": 1.0,
                         "price": [3e6, 12e6, 12e6]})
    base = fc.score(rows, 2028)
    avoid = fc.score(rows, 2028, fc.Behavior(entity_takeup=0.5))
    assert avoid["tax_now"].tolist() == base["tax_now"].tolist()
    assert avoid["tax_hd2"].iloc[0] == pytest.approx(base["tax_hd2"].iloc[0])        # under $4M
    top = dict(fc.entity_share())[10e6]
    assert avoid["tax_hd2"].iloc[1] == pytest.approx(base["tax_hd2"].iloc[1] * (1 - 0.5 * top))
    assert avoid["tax_hd2"].iloc[2] == pytest.approx(base["tax_hd2"].iloc[2])        # owner-occupant buyer


def test_sales_response_scales_with_the_added_tax():
    rows = pd.DataFrame({"fy": 2028, "category": ["nonowner"], "count": 1.0, "price": [5e6]})
    s = fc.score(rows, 2028, fc.Behavior(elasticity=6.0))
    d_pp = (s["tax_hd2_static"] - s["tax_now"]).iloc[0] / 5e6 * 100
    assert s["count_hd2"].iloc[0] == pytest.approx(np.exp(-0.06 * d_pp))
    first_year = fc.score(rows, 2028, replace(fc.Behavior(elasticity=6.0), forestall=(0.25, 1.5)))
    assert first_year["count_hd2"].iloc[0] < s["count_hd2"].iloc[0]


def test_mls_targets_read_the_band_file():
    m = fc.mls_targets()
    assert m.loc["Honolulu", 4] == pytest.approx(9.0 * 0.96)          # $10M+: MLS x unlisted-share adjustment
    assert m.loc["Honolulu", 2] == pytest.approx(130.4 * 1.059)       # $3-6M
    assert m.loc["Hawaii", 1] == pytest.approx(470.5)
    low = fc.mls_targets("mls_low")
    assert low.loc["Kauai", 3] == pytest.approx(7.5)
    np.testing.assert_allclose(low.loc["Maui"], m.loc["Maui"])       # Maui's own count stays central


def test_state_current_law_is_dotax_in_each_base_year(monkeypatch):
    # Anchored on DOTAX's collections: scored at its own base year, a year
    # gives back exactly what DOTAX recorded (the model's level error on
    # unmatched documents must not leak into the baseline).
    sales = fc.load_sales()
    for b in fc.STATE_BASE_FY:
        monkeypatch.setattr(fc, "STATE_BASE_FY", [b])
        assert fc.state_current_law(sales, b) == pytest.approx(fc.DOTAX_COLLECTIONS_M[b])


def test_behavior_cases_and_market_ranges_are_ordered():
    sales, t = fc.load_sales(), fc.TARGET_YEARS[0]

    def total(bh, **kw):
        return fc.by_county_change(fc.components(sales, t, bh), fc.stock_method(t, bh, **kw)).sum()
    static, central = total(fc.STATIC), total(fc.CENTRAL)
    assert static > central > 0
    assert total(fc.STATIC, mls_col="mls_low") < static < total(fc.STATIC, mls_col="mls_high")
    for channel in ("price_response", "owner_shift", "entity_takeup", "elasticity"):   # each channel costs revenue
        assert total(replace(fc.CENTRAL, **{channel: 0.0})) > central


def test_simulation_brackets_the_central_estimate():
    sim = fc.simulate(fc.load_sales(), n=40, years=[fc.TARGET_YEARS[0]])
    q = fc.simulation_summary(sim).iloc[0]
    sales, t = fc.load_sales(), fc.TARGET_YEARS[0]
    central = fc.by_county_change(fc.components(sales, t, fc.CENTRAL), fc.stock_method(t, fc.CENTRAL)).sum()
    assert q["p05"] < central < q["p95"]
    assert len(sim) == 40 and sim["elasticity"].between(1, 30).all()


def test_owner_shift_and_per_category_elasticity():
    rows = pd.DataFrame({"fy": 2028, "category": ["nonowner", "nonowner", "owner"], "count": 1.0,
                         "price": [1.5e6, 5e6, 5e6]})
    base = fc.score(rows, 2028)
    shift = fc.score(rows, 2028, fc.Behavior(owner_shift=4.0))
    assert shift["tax_hd2"].iloc[0] == pytest.approx(base["tax_hd2"].iloc[0], rel=0.2)   # small gap below $2M
    tax_fn, first_index = fc.BILLS[fc.BILL]
    idx = (1 + fc.CPI_GROWTH) ** (2028 - first_index)
    own, non = tax_fn(np.array([5e6]), "owner", index=idx)[0], base["tax_hd2"].iloc[1]
    s_ = min(0.04 * (non - own) / 5e6 * 100, fc.OWNER_SHIFT_CAP)
    assert shift["tax_hd2"].iloc[1] == pytest.approx((1 - s_) * non + s_ * own)
    assert shift["tax_hd2"].iloc[2] == pytest.approx(base["tax_hd2"].iloc[2])            # owner-occupant buyer
    split = fc.score(rows, 2028, fc.Behavior(elasticity={"owner": 0.0, "nonowner": 8.0}))
    assert split["count_hd2"].iloc[2] == pytest.approx(1.0) and split["count_hd2"].iloc[1] < 1.0


def test_price_response_taxes_the_lower_price():
    rows = pd.DataFrame({"fy": 2028, "category": ["nonowner"], "count": 1.0, "price": [5e6]})
    base = fc.score(rows, 2028)
    d_pp = (base["tax_hd2_static"] - base["tax_now"]).iloc[0] / 5e6 * 100
    low = fc.score(rows, 2028, fc.Behavior(price_response=1.0))
    tax_fn, first_index = fc.BILLS[fc.BILL]
    idx = (1 + fc.CPI_GROWTH) ** (2028 - first_index)
    assert low["tax_hd2"].iloc[0] == pytest.approx(tax_fn(np.array([5e6 * (1 - d_pp / 100)]), "nonowner", index=idx)[0])
    assert low["tax_now"].iloc[0] == pytest.approx(base["tax_now"].iloc[0])             # today's tax is unchanged


def test_developer_sales_respond_later_and_entities_grow():
    rows = pd.DataFrame({"fy": 2028, "category": ["nonowner"], "count": 1.0, "price": [12e6]})
    lag = fc.Behavior(elasticity=6.0, developer_ramp=fc.DEVELOPER_RAMP)
    y1, y5 = fc.score(rows, fc.EFFECTIVE_FY, lag), fc.score(rows.assign(fy=2032), fc.EFFECTIVE_FY + 4, lag)
    flat = fc.score(rows, fc.EFFECTIVE_FY, fc.Behavior(elasticity=6.0))
    dev = fc.developer_share()[1][-1]
    assert y1["count_hd2"].iloc[0] == pytest.approx((1 - dev) * flat["count_hd2"].iloc[0] + dev)   # no response yet
    flat5 = fc.score(rows.assign(fy=2032), fc.EFFECTIVE_FY + 4, fc.Behavior(elasticity=6.0))
    assert y5["count_hd2"].iloc[0] == pytest.approx(flat5["count_hd2"].iloc[0])                   # full response
    grow = fc.Behavior(entity_takeup=0.2, entity_growth=0.1)
    k1 = fc.score(rows, fc.EFFECTIVE_FY, grow)["tax_hd2"].iloc[0]
    k3 = fc.score(rows.assign(fy=2030), fc.EFFECTIVE_FY + 2, grow)["tax_hd2"].iloc[0]
    top = dict(fc.entity_share())[10e6]
    assert k1 / fc.score(rows, fc.EFFECTIVE_FY)["tax_hd2"].iloc[0] == pytest.approx(1 - 0.2 * top)
    assert k3 / fc.score(rows.assign(fy=2030), fc.EFFECTIVE_FY + 2)["tax_hd2"].iloc[0] == pytest.approx(1 - 0.24 * top)


def test_sales_history_repeats_the_base_years():
    hist, base = fc.load_sales_history(), fc.load_sales()
    h = hist[hist["fy"] >= min(fc.MAUI_BASE_FY)]
    for cat in ("owner", "nonowner", "nonres", "mf"):
        a, b = h[h["category"] == cat], base[base["category"] == cat]
        assert a["count"].sum() == pytest.approx(b["count"].sum())
        assert (a["count"] * a["price"]).sum() == pytest.approx((b["count"] * b["price"]).sum(), rel=1e-6)
    assert set(hist["fy"]) == set(range(2016, 2027))


def test_draft_raises_the_tax_just_below_2_million():
    for c in ("owner", "nonowner"):
        assert fc.BILLS[fc.BILL][0](np.array([1.999e6]), c)[0] > fc.current_law_tax(np.array([1.999e6]), c)[0]
        assert 1.5e6 < fc.lower_breakeven(c) < 2e6


def _count(bh, year: int, category: str = "nonowner", price: float = 12e6) -> tuple[float, float]:
    """(count under bh, added tax in points) for one sale in year *year* of the law."""
    fy = fc.EFFECTIVE_FY + year - 1
    rows = pd.DataFrame({"fy": fy, "category": [category], "count": 1.0, "price": [price]})
    st = fc.score(rows, fy)
    d = (st["tax_hd2_static"] - st["tax_now"]).iloc[0] / price * 100
    return fc.score(rows, fy, bh)["count_hd2"].iloc[0], d


def test_each_channel_by_year_of_the_law():
    assert fc.TARGET_YEARS[0] == fc.EFFECTIVE_FY == 2028
    over = fc.Behavior(elasticity=6.0, first_year=1.2)
    n1, d1 = _count(over, 1)
    n2, d2 = _count(over, 2)
    assert n1 == pytest.approx(np.exp(-0.06 * 1.2 * d1)) and n2 == pytest.approx(np.exp(-0.06 * d2))
    dev = fc.developer_share()[1][-1]
    lag = fc.Behavior(elasticity=6.0, developer_ramp=fc.DEVELOPER_RAMP)
    for year, r in zip((1, 2, 3, 4, 5, 6), (0, 0, 0.5, 0.75, 1, 1), strict=True):
        n, d = _count(lag, year)
        assert n == pytest.approx((1 - dev) * np.exp(-0.06 * d) + dev * np.exp(-0.06 * r * d))
    n, d = _count(fc.Behavior(elasticity=6.0, curvature=0.25), 2)
    assert n == pytest.approx(np.exp(-0.06 * d * (abs(d) / fc.CURVATURE_REF) ** 0.25))
    mult = fc.Behavior(elasticity=6.0, nonowner_mult=1.5)
    n, d = _count(mult, 2)
    assert n == pytest.approx(np.exp(-0.06 * 1.5 * d))
    n, d = _count(mult, 2, category="owner")
    assert n == pytest.approx(np.exp(-0.06 * d))                           # owner-occupant buyers: no multiplier
    assert _count(fc.Behavior(elasticity=6.0), 2, category="nonres")[0] == pytest.approx(1.0)
    c = fc.CENTRAL                                                          # the published central values
    assert (c.elasticity, c.first_year, c.curvature, c.developer_ramp, c.price_response, c.owner_shift,
            c.entity_takeup, c.entity_growth) == (6, 1.1, 0.125, fc.DEVELOPER_RAMP, 0.5, 2.0, 0.2, 0.075)


def test_developer_shares_apply_to_base_year_sales():
    # Value-weighted over Maui's base-year home sales, the looked-up share must
    # equal the table's, whatever year the sales are aged to.
    homes = fc.maui_homes(fc.load_sales())
    tab = pd.read_csv(fc.DATA / "maui_developer_share_fy2023_2026.csv")
    edges, dev = fc.developer_share()
    share = dev[np.searchsorted(edges, homes["price"].to_numpy(), side="right") - 1]
    w = homes["count"] * homes["price"]
    assert (share * w).sum() / w.sum() == pytest.approx(tab["developer_price"].sum() / tab["sum_price"].sum(), rel=0.02)


def test_simulation_draws_reach_every_county(monkeypatch):
    sales = fc.load_sales()
    sim = fc.simulate(sales, n=40, years=[2028])
    for c in ("honolulu", "hawaii", "kauai", "maui"):
        assert sim["elasticity"].rank().corr(sim[f"change_{c}_$M"].rank()) < -0.4
    # Behavior fixed: Maui has no listing-data draw; the other counties spread
    # around their central values.
    monkeypatch.setattr(fc, "draw_behaviors", lambda n, seed=fc.SEED: ([fc.CENTRAL] * n, np.random.default_rng(seed)))
    fixed = fc.simulate(sales, n=40, years=[2028])
    central = fc.by_county_change(fc.components(sales, 2028, fc.CENTRAL), fc.stock_method(2028, fc.CENTRAL))
    assert fixed["change_maui_$M"].std() < 1e-9
    for c in ("Honolulu", "Hawaii", "Kauai"):
        col = fixed[f"change_{c.lower()}_$M"]
        assert col.std() > 0.3 and abs(col.median() - central[c]) < 0.15 * central[c]


def test_market_cycle_repeats_the_estimate():
    cyc = fc.market_cycle({"static": fc.STATIC, "central": fc.CENTRAL}).set_index("fy")
    sales, hist = fc.load_sales(), fc.load_sales_history()
    for lab, bh in (("static", fc.STATIC), ("central", replace(fc.CENTRAL, first_year=1.0, developer_ramp=None))):
        ref = fc.by_county_change(fc.components(sales, fc.EFFECTIVE_FY, bh), fc.stock_method(fc.EFFECTIVE_FY, bh)).sum()
        assert cyc.loc[2023:2026, f"state_change_{lab}_fy2028_prices_$M"].mean() == pytest.approx(ref, rel=1e-6)
        for y in (2016, 2022):
            m = fc.components(hist[hist["fy"] == y], y, bh)
            assert cyc.loc[y, f"maui_change_{lab}_own_prices_$M"] == pytest.approx(m["hd2"] - m["now"], rel=1e-9)
        tot = cyc[f"total_{lab}_own_prices_$M"] - cyc[f"state_change_{lab}_own_prices_$M"]
        assert tot.loc[2022] == pytest.approx(fc.DOTAX_COLLECTIONS_BY_SALE_M[2022])     # collections timed by sale
    assert cyc.loc[2022, "state_change_static_fy2028_prices_$M"] > cyc.loc[2022, "state_change_static_own_prices_$M"]
