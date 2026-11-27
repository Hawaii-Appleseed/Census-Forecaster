"""The general excise tax in full: input-output burden, Table 1.1, scorers."""
import json

import numpy as np
import pytest
from state_revenue import general_excise as ge
from state_revenue import load_ledger
from state_revenue.annual_report import TableNotFound, parse_get_activity

pytestmark = pytest.mark.smoke

TABLE_1_1 = """
                              TABLE 1.1 - GENERAL EXCISE AND USE TAX BASE AND TAXES FOR FISCAL
                                  YEARS ENDING JUNE 30, 2020 AND 2019
                                                (In Thousands of Dollars)
SOURCE OF REVENUE                          Rate                FY 2020                      FY 2019                       Amount
TAX BASE
Retailing                                                  $ 35,679,505                 $ 36,800,989                 $ (1,121,484)
Trans. Accom. Rentals                                         5,214,531                    5,669,106                     (454,575)
All Others (4%)                                               5,892,817                    6,542,739                     (649,922)
    Subtotal                                               $ 46,786,853                 $ 49,012,834                 $ (2,225,981)
Use (1/2%)                                                    6,885,687                    7,279,558                     (393,871)
Insurance Solicitors                                            702,176                      753,946                      (51,770)
TOTAL—ALL ACTIVITIES                                       $ 54,374,716                 $ 57,046,338
TAX
Retailing                                  4.00%           $     1,427,180              $     1,472,040              $        (44,859)
Trans. Accom. Rentals                      4.00%                   208,581                      226,764                       (18,183)
All Others (4%)                            4.00%                   235,713                      261,710                       (25,997)
Use (1/2%)                                 0.50%                    34,428                       36,398                        (1,969)
Insurance Solicitors                       0.15%                     1,053                        1,131                           (78)
      Unallocated 1                                        $         34,405             $          35,650            $          (1,245)
TOTAL - ALL ACTIVITIES                                     $     1,941,360              $     2,033,693
"""


def test_parse_table_1_1():
    t = parse_get_activity(TABLE_1_1)
    assert t.years == (2020, 2019)
    assert t.base[2020] == {"retailing": 35_679_505, "transient_accommodations": 5_214_531,
                            "other_4pct": 5_892_817, "use": 6_885_687,
                            "insurance_commissions": 702_176}
    assert t.tax[2020]["unallocated"] == 34_405
    assert t.rates == {"retailing": 0.04, "transient_accommodations": 0.04, "other_4pct": 0.04,
                       "use": 0.005, "insurance_commissions": 0.0015}
    assert t.tax_totals[2020] == sum(t.tax[2020].values())
    assert t.unmatched == ()
    with pytest.raises(TableNotFound):
        parse_get_activity("nothing")


def test_bundled_activity_tax_equals_collections():
    payload = json.loads((ge.DATA / "annual_collections.json").read_text())
    ledger = load_ledger()
    for fy, row in payload["general_excise_by_activity"].items():
        assert row["tax_total"] == ledger[int(fy)].lines["general_excise"], fy
        assert abs(row["residual"]) <= 25, fy


def test_io_table_balances():
    io = ge.load_io()
    assert len(io.codes) == 62
    total_final = sum(io.final.values())
    assert np.allclose(io.leontief @ total_final, io.output, rtol=1e-6, atol=1e-3)
    assert io.rows["state_get"].sum() == pytest.approx(4117.0, abs=0.5)


def test_burden_accounts_for_every_dollar():
    io = ge.load_io()
    b = ge.burden_by_purchaser(io)
    assert sum(x.total for x in b) == pytest.approx(io.rows["state_get"].sum(), rel=1e-6)
    s = ge.shares(b)
    assert s["residents"] == pytest.approx(0.511, abs=0.002)
    assert s["visitors"] == pytest.approx(0.265, abs=0.002)
    # One activity's sectors carry exactly the tax those sectors pay.
    sectors = ge.ACTIVITY_SECTORS["transient_accommodations"]
    acc = ge.burden_by_purchaser(io, sectors=sectors)
    assert sum(x.total for x in acc) == pytest.approx(io.rows["state_get"][io.index(sectors)].sum())
    assert ge.shares(acc)["visitors"] > 0.6


def test_rate_change_is_rate_times_base():
    fy, row = ge._latest_activity()
    s = ge.score_rate_change({"retailing": 0.045}, fy)
    assert s.state == pytest.approx(0.005 * row["base"]["retailing"])
    assert s.county == 0.0
    assert sum(s.borne_by.values()) == pytest.approx(1.0)
    with pytest.raises(KeyError):
        ge.score_rate_change({"gambling": 0.05}, fy)


def test_rate_change_grows_with_the_baseline():
    fy, row = ge._latest_activity()
    growth = {fy + 2: load_ledger()[fy].lines["general_excise"] * 1.1}
    s = ge.score_rate_change({"services": 0.05}, fy + 2, baseline_get=growth)
    assert s.state == pytest.approx(0.01 * row["base"]["services"] * 1.1)


def test_grocery_exemption_matches_resident_model_and_adds_visitors():
    s = ge.score_category_exemption("groceries", 2025)
    residents = s.detail["resident_spending"] * ge.STATE_RATE
    # The resident incidence model's grocery figure (GET_INCIDENCE_METHODOLOGY.md): $251M.
    assert residents / 1e3 == pytest.approx(251, abs=5)
    assert s.borne_by["visitors"] == pytest.approx(0.18, abs=0.02)
    assert s.county == pytest.approx(s.state * ge.COUNTY_RATE / ge.STATE_RATE)


def test_sector_exemption_relief_adds_up():
    s = ge.score_sector_exemption(("S50", "S51"), 2025)
    assert sum(s.borne_by.values()) == pytest.approx(1.0)
    assert sum(s.detail.values()) == pytest.approx(s.state)
    b2b = ge.score_sector_exemption(("S40", "S43"), 2025, purchasers=("businesses",))
    assert b2b.state < 0 and sum(b2b.borne_by.values()) == pytest.approx(1.0)
    # All sales of a sector = its whole tax, scaled to the fiscal year.
    io = ge.load_io()
    whole = ge.score_sector_exemption(("S55",), 2025)
    scale = load_ledger()[2025].lines["general_excise"] / (io.rows["state_get"].sum() * 1e3)
    assert -whole.state == pytest.approx(io.rows["state_get"][io.index(("S55",))].sum() * 1e3 * scale,
                                         rel=1e-6)


def test_visitor_spending_file():
    years = ge.load_visitor_spending()
    assert {2023, 2024, 2025} <= set(years)
    for row in years.values():
        parts = sum(v for k, v in row.items() if k != "total")
        assert parts == pytest.approx(row["total"], abs=2)
    assert years[2025]["groceries"] == pytest.approx(1076.8, abs=0.1)
