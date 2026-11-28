"""The bundled ledger of state tax collections and the line catalogue."""
import pytest
from state_revenue import BY_KEY, LINES, TIERS, load_ledger
from state_revenue.lines import HOST

pytestmark = pytest.mark.smoke


def test_every_year_adds_to_its_printed_total():
    ledger = load_ledger()
    assert ledger.fiscal_years == list(range(2004, 2026))
    for fy in ledger.fiscal_years:
        assert abs(ledger[fy].residual) <= 25, fy


def test_fy2025_matches_the_annual_report():
    # DOTAX Annual Report FY2025, Table 1.8.
    y = load_ledger()[2025]
    assert y.total == 11_456_335
    assert y.lines["general_excise"] == 4_630_396
    assert y.lines["individual_income"] == 3_288_093
    assert y.lines["transient_accommodations"] == 816_184
    assert y.lines["estate"] == 405_766
    assert y.report == "25annrpt.pdf"


def test_coverage_shares_sum_to_one():
    ledger = load_ledger()
    for fy in ledger.fiscal_years:
        assert sum(ledger.coverage(fy).values()) == pytest.approx(1.0, abs=1e-5)
    cov = ledger.coverage(2025)
    assert cov["structural"] == pytest.approx(0.295, abs=0.001)
    assert cov["structural"] + cov["base_rate"] == pytest.approx(0.872, abs=0.001)


def test_folded_lines_before_fy2019():
    ledger = load_ledger()
    assert ledger[2018].folded == {"environmental_response": "fuel",
                                   "rental_vehicle": "motor_vehicle"}
    assert ledger[2019].folded == {}
    assert min(ledger.series("environmental_response")) == 2019


def test_series_and_unknown_line():
    ledger = load_ledger()
    surcharge = ledger.series("county_surcharge")
    assert min(surcharge) == 2007                    # Honolulu's surcharge began January 2007
    with pytest.raises(KeyError):
        ledger.series("gross_receipts")


def test_catalogue():
    assert {line.tier for line in LINES} == set(TIERS)
    aliases = [a for line in LINES for a in line.aliases]
    assert len(aliases) == len(set(aliases)), "an alias names two lines"
    assert all(k in BY_KEY and h in BY_KEY for k, h in HOST.items())
    assert {k for k, line in BY_KEY.items() if line.tier == "structural"} == {
        "individual_income", "conveyance"}


def test_general_fund_ledger():
    gf = load_ledger("general_fund")
    assert gf.fiscal_years == list(range(2004, 2026))
    assert gf[2025].total == 9_974_161                 # FY2025 annual report, Table 1.9
    for fy in gf.fiscal_years:
        assert abs(gf[fy].residual) <= 25, fy
        assert gf[fy].total < load_ledger()[fy].total
    with pytest.raises(ValueError):
        load_ledger("special_funds")


def test_fuel_split_and_tat_distribution():
    from state_revenue.ledger import fuel_split, tat_distribution
    for fy in range(2004, 2026):
        split = fuel_split(fy)
        assert split["state"] + split["county"] == load_ledger()[fy].lines["fuel"]
        assert split["state"] > 0 and split["county"] > 0
    assert fuel_split(2025) == {"state": 80_205, "county": 87_761}
    d = tat_distribution(2025)
    assert d["Convention Center Fund"] == 11_000
    assert sum(d.values()) == 816_200                  # the tax and its fees
    with pytest.raises(KeyError):
        tat_distribution(2005)
