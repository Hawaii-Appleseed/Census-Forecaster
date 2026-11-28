"""The PDF backfill of DOTAX's monthly collections and the bundle it filled."""
import json
from importlib.resources import files

import pytest
from census_forecaster.scripts import backfill_dotax_collections as bf
from census_forecaster.scripts.refresh_dotax_collections import TAX_LINE_KEYS

pytestmark = pytest.mark.smoke

# The layout since FY2022: the month, then the same month a year before.
NEW = """
STATE OF HAWAII                              STATE TAX COLLECTIONS AND DISTRIBUTION ‐ MARCH 2024
                                                   MONTHLY TOTALS             CUMULATIVE TOTALS
SOURCE OF REVENUE                               MAR 2024        MAR 2023      2023‐2024    2022‐2023
   Banks/Financial Corp. 1/, 7/                   2,005,030       (2,661,944)    18,451,830   19,606,257
   General Excise & Use 2/                      353,757,683      342,416,596  3,365,425,372  3,307,153,276
   Income‐Ind.: 1/
      Payment W/Returns                          39,934,038       46,868,123    189,613,363    214,578,844
      Refunds                                  (147,851,873)    (125,695,123)  (541,041,359)  (797,872,103)
TOTAL ‐ MARCH 2024                              247,844,878                   7,883,108,554
TOTAL ‐ MARCH 2023                              260,927,652      260,927,652
   State General Fund                           999,999,999      999,999,999
"""

# Through FY2021: four tax-district columns first.
OLD = """
STATE OF HAWAII                                     STATE TAX COLLECTIONS AND DISTRIBUTION - NOVEMBER 2016
                                 FIRST       SECOND      THIRD      FOURTH     ALL DISTRICTS
SOURCE OF REVENUE               DISTRICT    DISTRICT    DISTRICT   DISTRICT    NOV 2016     NOV 2015
Fuel 7/                         4,541,680   1,105,352   1,496,414    256,551    7,399,996    7,100,000
Fuel & Environmental            1,000,000           0           0          0    1,000,000      900,000
Honolulu County Surcharge 3/   20,285,427           0          35          0   20,285,462   19,000,000
TOTAL - NOVEMBER 2016          25,827,107   1,105,352   1,496,449    256,551   28,685,458
"""


def test_new_layout():
    out, unmatched = bf.parse_collec_text(NEW, 2024, 3)
    assert unmatched == []
    assert out["2024-03"] == {"banks": 2_005_030, "ge_use": 353_757_683, "ind_ret": 39_934_038,
                              "ind_refunds": -147_851_873, "total": 247_844_878}
    assert out["2023-03"]["banks"] == -2_661_944
    assert bf.check(out["2024-03"]) == 0
    assert "general_fund" not in out["2024-03"]          # nothing after the total is read


def test_district_layout_reads_the_statewide_columns():
    out, unmatched = bf.parse_collec_text(OLD, 2016, 11)
    assert unmatched == []
    assert out["2016-11"]["fuel"] == 7_399_996            # not the first district's 4,541,680
    assert out["2015-11"]["county_surcharge"] == 19_000_000
    assert out["2016-11"][bf.COMBINED_FUEL] == 1_000_000  # counted in the check, not stored
    assert bf.check(out["2016-11"]) == 0


def test_unknown_row_is_reported():
    out, unmatched = bf.parse_collec_text(NEW.replace("General Excise & Use 2/", "Mystery Levy"), 2024, 3)
    assert unmatched == ["mystery levy"]


def test_bundle_is_complete_and_matches_the_annual_reports():
    path = files("census_forecaster") / "data" / "dotax_monthly" / "collections.json"
    monthly = json.loads(path.read_text())["monthly"]
    months = [f"{y}-{m:02d}" for y in range(2013, 2026) for m in range(1, 13)
              if "2013-07" <= f"{y}-{m:02d}" <= "2025-12"]
    for m in months:
        assert {"ind_wh", "ind_est", "ind_ret", "ind_refunds", "ge_use", "tat"} <= set(monthly[m]), m
        assert bf.COMBINED_FUEL not in monthly[m]
        if m <= "2025-06" and "total" in monthly[m]:
            lines = sum(v for k, v in monthly[m].items() if k in TAX_LINE_KEYS)
            assert monthly[m]["total"] == pytest.approx(lines, abs=5), m
    # Twelve months are the annual report's fiscal year (thousands; DOTAX Annual Reports, Table 1.8).
    annual = {2015: (1_988_194, None), 2019: (2_568_616, 3_541_389),
              2024: (3_281_525, 4_481_056), 2025: (3_288_093, 4_630_396)}
    for fy, (income, excise) in annual.items():
        ms = [f"{fy - 1}-{m:02d}" for m in range(7, 13)] + [f"{fy}-{m:02d}" for m in range(1, 7)]
        got = sum(monthly[m][k] for m in ms for k in ("ind_wh", "ind_est", "ind_ret", "ind_refunds"))
        assert got / 1e3 == pytest.approx(income, abs=1), fy
        if fy != 2015:
            assert sum(monthly[m]["ge_use"] for m in ms) / 1e3 == pytest.approx(excise, abs=1), fy
