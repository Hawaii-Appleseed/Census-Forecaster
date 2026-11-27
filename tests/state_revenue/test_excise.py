"""Transient accommodations and per-unit excise scorers, and their bases."""
from datetime import date

import pytest
from state_revenue import excise as ex
from state_revenue import load_ledger
from state_revenue.scripts.refresh_excise_bases import parse_fuels, parse_tobacco

pytestmark = pytest.mark.smoke

FUEL_SUMMARY = """
    TOTAL                                                                                       7,476,043.89                  5,530,733.42               13,006,777.31
          SUMMARY
Gasoline                                                        421,543,837                   67,403,071.71                 79,519,760.87               146,922,832.58
Diesel Oil (off-highway)                                        175,119,451                    1,751,194.51                                               1,751,194.51
Diesel Oil (highway)                                             43,769,449                    7,143,543.52                   8,640,779.06               15,784,322.58
Liquefied Petroleum Gas (highway)                                     (6,923)                       (355.45)                      (255.66)                     (611.11)
Small Boats (gasoline)                                             1,272,941                     203,670.56                     236,765.04                  440,435.60
Small Boats (diesel oil)                                         (2,758,526)                     (27,585.26)                                                (27,585.26)
Aviation Fuel                                                   249,738,049                    2,527,699.41                                               2,527,699.41
Other Fuel 1/                                                    10,562,209                      235,714.86                    114,876.28                   350,591.14
  SUB-TOTAL                                                     899,240,488                   79,236,953.86                 88,511,925.59               167,748,879.45 **
ERT - Petroleum Products (barrel) 2/                             24,624,198                   25,855,407.90                                              25,855,407.90
ERT - Fossil Fuels (MMBtu) 2/                                      5,823,582                    1,106,480.58                                               1,106,480.58
"""

TOBACCO = """
NET TAX COLLECTIONS
Cigarettes                                       57,157,512.00      2,722,944.00       3,111,936.00      1,021,104.00    64,013,496.00     74,498,786.57    (10,485,290.57
All Other Tobacco Products                        8,696,046.04            224.61           5,704.13              0.00     8,701,974.78      7,993,728.25        708,246.53
E-liquids                                         6,455,961.11         54,615.22         169,643.29         59,491.94     6,739,711.56        n/a             6,739,711.56
Cigars                                            2,029,805.86          8,477.37             478.85        137,504.18     2,176,266.26      1,941,935.37        234,330.89
Little Cigars                                        76,783.32            108.00               0.00              0.00        76,891.32         88,025.68        (11,134.36
  License Fees                                       38,750.00          2,250.00           2,750.00          1,750.00        45,500.00         13,252.50         32,247.50
  Retail Permits                                     48,230.00          5,260.00           5,820.00          1,300.00        60,610.00         57,330.00          3,280.00
  Unallocated Net Collections                       580,048.61            285.00             275.00       (145,753.90)      434,854.71       (606,873.88)     1,041,728.59
"""


def test_parse_fuel_summary():
    f = parse_fuels(FUEL_SUMMARY)
    assert f["gasoline"] == {"quantity": 421_543_837, "state_tax": 67_403_071.71,
                             "county_tax": 79_519_760.87}
    assert f["aviation"]["county_tax"] == 0.0
    assert f["lpg_highway"]["quantity"] == -6_923
    assert f["ert_barrel"]["state_tax"] / f["ert_barrel"]["quantity"] == pytest.approx(1.05)
    state = sum(v["state_tax"] for k, v in f.items() if not k.startswith("ert_"))
    assert state == pytest.approx(79_236_953.86)


def test_parse_tobacco():
    t = parse_tobacco(TOBACCO)
    assert t["cigarettes"] == 64_013_496.00
    assert t["unallocated"] == 434_854.71
    assert sum(t.values()) == pytest.approx(82_249_304.63)


def test_rate_in_weights_by_days():
    assert ex.rate_in("transient_accommodations", 2025) == pytest.approx(0.1025)
    # FY2026: July-December 2025 at 10.25%, January-June 2026 at 11%.
    first = (date(2026, 1, 1) - date(2025, 7, 1)).days
    assert ex.rate_in("transient_accommodations", 2026) == pytest.approx(
        (0.1025 * first + 0.11 * (365 - first)) / 365)
    assert ex.rate_in("transient_accommodations", 2032) == pytest.approx(0.0925)
    assert ex.rate_in("rental_vehicle", 2026) == pytest.approx(7.0)
    assert ex.rate_in("cigarettes", 2025) == pytest.approx(0.16)


def test_tat_score():
    revenue = load_ledger()[2025].lines["transient_accommodations"]
    assert ex.score_tat(0.1025, 2025).state == pytest.approx(0.0, abs=1e-6)
    s = ex.score_tat(0.1125, 2025)
    assert s.state == pytest.approx(revenue * 0.01 / 0.1025)
    assert ex.score_tat(0.1125, 2025, elasticity=-0.5).state < s.state
    assert s.borne_by["visitors"] > 0.6


def test_fuel_rates_and_score():
    r = ex.fuel_rates()
    assert r["gasoline"] == pytest.approx(0.16, abs=0.001)
    assert r["aviation"] == pytest.approx(0.01, abs=0.0005)
    data_fy = ex.load_bases()["calendar_year"] + 1
    gallons = ex.load_bases()["fuel"]["gasoline"]["quantity"]
    s = ex.score_fuel({"gasoline": r["gasoline"] + 0.05}, data_fy)
    assert s.state == pytest.approx(0.05 * gallons / 1e3)
    with pytest.raises(KeyError):
        ex.score_fuel({"jet_a": 0.02}, data_fy)


def test_cigarettes_and_rental_vehicles():
    data_fy = ex.load_bases()["calendar_year"] + 1
    assert ex.cigarettes_sold(data_fy) == pytest.approx(64_013_496 / 0.16)
    s = ex.score_cigarettes(0.21, data_fy)
    assert s.state == pytest.approx(0.05 * 64_013_496 / 0.16 / 1e3)
    assert ex.score_cigarettes(0.21, data_fy, elasticity=-0.4, pack_price=12.0).state < s.state
    rv = ex.score_rental_vehicle(ex.rate_in("rental_vehicle", 2025) + 1.0, 2025)
    revenue = load_ledger()[2025].lines["rental_vehicle"]
    assert rv.state == pytest.approx(revenue / ex.rate_in("rental_vehicle", 2025))


def test_proportional():
    revenue = load_ledger()[2025].lines["liquor"]
    assert ex.score_proportional("liquor", 1.1, 2025).state == pytest.approx(0.1 * revenue)
