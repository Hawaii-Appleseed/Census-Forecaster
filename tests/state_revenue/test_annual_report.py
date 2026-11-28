"""The annual report's "Tax Collections" table parser and the merge across reports."""
import pytest
from state_revenue.annual_report import TableNotFound, parse_tax_collections, residuals
from state_revenue.scripts.refresh_annual_collections import merge, report_urls

pytestmark = pytest.mark.smoke

# The FY2007 report's layout: no footnote numbers, a dash for a line that did
# not exist yet, unnumbered table.
REPORT_2007 = """
TAX COLLECTIONS

Total tax collections in FY 2007 amounted to $5.3 billion.

                                             TAX COLLECTIONS
                                           (In Thousands of Dollars)

                                                           FY 2007                                FY 2006
                                                     Amount        % of                     Amount        % of
SOURCE OF REVENUE                                    Collected     Total                    Collected     Total
General Excise & Use Tax                           2,555,762           48.07              2,355,316            46.17
Honolulu County Surcharge                             53,805            1.01                 -                  -
Income – Individuals                               1,560,286           29.35              1,550,596            30.40
Motor Vehicle Tax*                                   112,412            2.11                107,527             2.11
    TOTAL                                        $ 4,282,265          100.00            $ 4,013,439           100.00
* Includes motor vehicle weight tax.
"""

# The FY2025 report's layout: superscript footnote numbers after labels, a
# label wrapped onto two lines (FY2010 report), percent signs (FY2012 report).
REPORT_2025 = """
                                                TABLE 1.8 - TAX COLLECTIONS 4
                                                    (In Thousands of Dollars )

                                                                          FY 2025                                          FY 2024
                                                               Amount                   % of                       Amount          % of
SOURCE OF REVENUE                                              Collected                Total                      Collected       Total

Banks - Financial Corps.                                   $      10,296                   0.09                $       30,912                  0.28
Employment Security
Contributions                                                    281,225                   2.45                       377,458                  3.41
Fuel 3, 4                                                        167,966                  1.47%                       168,586                  1.52%
County Surcharge 5                                               565,256                   4.93                       489,688                  4.42
Motor Vehicle Tax and Fees 1/                                    145,418                   1.27                       143,094                  1.29
All Others 2                                                         638                   0.01                          650                    0.01
    TOTAL                                                  $   1,170,800                 100.00                  $  1,210,388                  100.00
1
  Includes the State motor vehicle weight tax.
"""


def test_old_layout():
    t = parse_tax_collections(REPORT_2007)
    assert t.years == (2007, 2006)
    assert t.lines[2007] == {"general_excise": 2_555_762, "county_surcharge": 53_805,
                             "individual_income": 1_560_286, "motor_vehicle": 112_412}
    assert "county_surcharge" not in t.lines[2006]       # a dash: no such line yet
    assert t.unmatched == ()
    assert residuals(t) == {2007: 0, 2006: 0}


def test_new_layout_footnotes_wraps_and_percent_signs():
    t = parse_tax_collections(REPORT_2025)
    assert t.years == (2025, 2024)
    assert t.lines[2025] == {"banks": 10_296, "unemployment": 281_225, "fuel": 167_966,
                             "county_surcharge": 565_256, "motor_vehicle": 145_418,
                             "all_others": 638}
    assert t.unmatched == ()
    assert residuals(t) == {2025: 1, 2024: 0}      # rounding in the printed total


def test_unknown_row_is_reported():
    t = parse_tax_collections(REPORT_2007.replace("Motor Vehicle Tax*", "Mystery Levy"))
    assert t.unmatched == ("mystery levy",)


def test_no_table():
    with pytest.raises(TableNotFound):
        parse_tax_collections("TAX COLLECTIONS\nnothing here\n")


def test_merge_prefers_newest_report_that_adds_up():
    older = parse_tax_collections(REPORT_2007)
    # A newer report revises FY2007 but misprints one line: its FY2007 lines
    # miss its own total, so FY2007 comes from the older report.
    header = "FY 2007                                FY 2006"
    assert header in REPORT_2007
    newer_text = (REPORT_2007.replace(header, "FY 2008                                FY 2007")
                  .replace("107,527", "117,527"))              # typo in the FY2007 column
    newer = parse_tax_collections(newer_text)
    assert newer.years == (2008, 2007)
    merged = merge({"07annrpt.pdf": older, "08annrpt.pdf": newer})
    assert merged[2007]["report"] == "07annrpt.pdf"
    assert merged[2008]["report"] == "08annrpt.pdf"
    assert merged[2006]["report"] == "07annrpt.pdf"


def test_report_urls_newest_link_per_year():
    html = ('<a href="https://files.hawaii.gov/tax/stats/stats/annual/25annrpt.pdf">'
            '<a href="https://files.hawaii.gov/tax/stats/stats/annual/archive/24annrpt_2.pdf">'
            '<a href="https://files.hawaii.gov/tax/stats/stats/annual/archive/99annrpt.pdf">'
            '<a href="https://files.hawaii.gov/tax/stats/stats/annual/archive/03annrpt-rev.pdf">')
    urls = report_urls(html, since=2003)
    assert list(urls) == [2003, 2024, 2025]
    assert urls[2024].endswith("24annrpt_2.pdf")


# The FY2005 and FY2024 general fund tables: a negative amount and percent in
# parentheses, and a doubled dollar sign.
GENERAL_FUND = """
                                                TABLE 1.9 - STATE GENERAL FUND
                                                     (In Thousands of Dollars)
                                                                  FY 2024                                              FY 2023
                                                      Amount                     % of                          Amount          % of
  SOURCE OF REVENUE                                   Collected                  Total                         Collected       Total
  Banks - Financial Corps.                     $      $28,912                0.30                    $      (534)        (0.02)
  Trans. Accom. Tax                                  715,475                 7.48                           756,578        8.22
  Environmental Response Tax                          21,968                 0.23                            22,074        0.24
      TOTAL                                        $766,355            100.00                          $ 778,118    100.00
"""


def test_general_fund_table():
    t = parse_tax_collections(GENERAL_FUND, "general_fund")
    assert t.years == (2024, 2023)
    assert t.lines[2024] == {"banks": 28_912, "transient_accommodations": 715_475,
                             "environmental_response": 21_968}
    assert t.lines[2023]["banks"] == -534
    assert residuals(t) == {2024: 0, 2023: 0}
    with pytest.raises(TableNotFound):
        parse_tax_collections(GENERAL_FUND)          # no collections table in it


FUEL_ALLOCATION = """
                                                          TABLE 1.6 - ALLOCATION OF FUEL TAXES
                                                                    (In Thousands of Dollars)
                                                                                                FY 2025                 FY 2024                 Amount
STATE HIGHWAY FUND
       Gasoline                                                                                  $ 66,909               $ 67,063              $      (154)
          Subtotal                                                                               $ 75,909               $ 76,446              $   (537)
          TOTAL                                                                                  $ 339,306              $ 325,187             $ 14,119
COUNTY HIGHWAY FUNDS
       City and County of Honolulu                                                               $ 44,814               $ 45,100              $      (286)
       County of Maui                                                                              16,300                 16,200                      100
          Subtotal                                                                               $ 87,761               $ 87,817              $       (56)
"""

TAT_TABLE = """
                                       TABLE 1.4 - TRANSIENT ACCOMMODATIONS TAX
                                                     (In Thousands of Dollars)
                                                                   FY 2025                  FY 2024                 Amount
Collection
   Trans. Accom./Time Share Occ. Tax                           $ 816,184                $ 828,327               $       (12,144)
     Total                                                     $ 816,200                $ 828,346               $       (12,147)
Distribution 1
       Turtle Bay Special Fund                                 $   1,500                $   1,500               $           -
       Tourism Emergency Special Fund                                -                        -                             -
       Mass Transit Fund                                          78,758                   79,668                          (910)
       General Fund                                              685,637                  715,493                       (29,856)
        Total                                                  $ 766,895                $ 796,661               $       (12,147)
"""


def test_county_fuel_and_tat_distribution():
    from state_revenue.annual_report import parse_county_fuel, parse_tat_distribution
    assert parse_county_fuel(FUEL_ALLOCATION) == {2025: 87_761, 2024: 87_817}
    assert parse_county_fuel("no table") == {}
    d = parse_tat_distribution(TAT_TABLE)
    assert d[2025] == {"Turtle Bay Special Fund": 1_500, "Tourism Emergency Special Fund": 0,
                       "Mass Transit Fund": 78_758, "General Fund": 685_637}
    assert d[2024]["Mass Transit Fund"] == 79_668
    assert parse_tat_distribution("no table") == {}
