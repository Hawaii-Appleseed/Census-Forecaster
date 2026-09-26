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
    c = {k: total(bh) for k, bh in fc.CASES.items()}
    assert c["static"] > c["behavioral_high"] > c["behavioral"] > c["behavioral_low"] > 0
    assert total(fc.STATIC, mls_col="mls_low") < c["static"] < total(fc.STATIC, mls_col="mls_high")


def test_owner_certification_and_per_category_elasticity():
    rows = pd.DataFrame({"fy": 2028, "category": ["nonowner", "nonowner", "owner"], "count": 1.0,
                         "price": [3e6, 5e6, 5e6]})
    base = fc.score(rows, 2028)
    shift = fc.score(rows, 2028, fc.Behavior(cert_shift=0.5))
    assert shift["tax_hd2"].iloc[0] == pytest.approx(base["tax_hd2"].iloc[0])        # under $4M: unchanged
    tax_fn, first_index = fc.BILLS[fc.BILL]
    owner_tax = tax_fn(np.array([5e6]), "owner", index=(1 + fc.CPI_GROWTH) ** (2028 - first_index))[0]
    assert shift["tax_hd2"].iloc[1] == pytest.approx(0.5 * base["tax_hd2"].iloc[1] + 0.5 * owner_tax)
    split = fc.score(rows, 2028, fc.Behavior(elasticity={"owner": 0.0, "nonowner": 8.0}))
    assert split["count_hd2"].iloc[2] == pytest.approx(1.0) and split["count_hd2"].iloc[1] < 1.0


def test_draft_raises_the_tax_just_below_2_million():
    for c in ("owner", "nonowner"):
        assert fc.BILLS[fc.BILL][0](np.array([1.999e6]), c)[0] > fc.current_law_tax(np.array([1.999e6]), c)[0]
        assert 1.5e6 < fc.lower_breakeven(c) < 2e6
