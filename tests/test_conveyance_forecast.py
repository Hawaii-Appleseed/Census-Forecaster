"""forecast_conveyance_sb3028.py: the statewide housing-stock method."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

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
