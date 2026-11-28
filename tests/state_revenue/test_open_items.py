"""Income tax cash timing, the investment pass-through and the liquor scorer."""
import pytest
from state_revenue import excise as ex
from state_revenue import general_excise as ge
from state_revenue import income_timing as it
from state_revenue import load_ledger
from state_revenue.scripts.refresh_excise_bases import parse_niaaa

pytestmark = pytest.mark.smoke


def test_tax_year_cash_adds_to_fiscal_years():
    c = it.tax_year_cash(2024)
    assert c.withholding_early / 1e6 == pytest.approx(1417, abs=1)
    assert c.refunds < 0 and c.net == pytest.approx(c.gross + c.refunds)
    assert it.tax_year_cash(2031) is None


def test_timing_shares():
    t = it.timing()
    assert 2020 not in t.years and 2021 not in t.years
    assert t.withholding_share + t.estimates_share + t.returns_share == pytest.approx(1.0)
    assert t.withholding_early == pytest.approx(0.50, abs=0.01)
    early, late = it.fiscal_shares("withholding")
    assert early == pytest.approx(0.462, abs=0.01) and early + late == pytest.approx(1.0)
    assert it.fiscal_shares("filing") == (0.0, 1.0)
    with pytest.raises(ValueError):
        it.fiscal_shares("payroll")


def test_to_fiscal_years_conserves_the_change():
    out = it.to_fiscal_years({2027: -100.0, 2028: -100.0})
    assert sum(out.values()) == pytest.approx(-200.0)
    assert list(out) == [2027, 2028, 2029]
    assert it.to_fiscal_years({2027: -100.0}, "filing") == {2027: 0.0, 2028: -100.0}


def test_investment_passed_on_keeps_every_dollar():
    burdens = ge.burden_by_purchaser()
    passed = ge.allocate_investment(burdens)
    total = sum(b.total for b in burdens)
    assert "investment" not in passed
    assert sum(passed.values()) == pytest.approx(total)
    before = {b.purchaser: b.total for b in burdens}
    assert all(passed[k] > before[k] for k in passed)
    assert passed["residents"] / total == pytest.approx(0.589, abs=0.003)
    assert passed["visitors"] / total == pytest.approx(0.298, abs=0.003)


def test_niaaa_parser_and_liquor_check():
    text = ("2024 15 1    2459653   1010917\n2024 15 2    5132156    662048\n"
            "2024 15 3   31250000   1406250\n2024 15 4          .   3079215\n"
            "2024  6 1   99999999   1\n1975 15 1    1970000    847100\n")
    assert parse_niaaa(text) == {2024: {"spirits": 2_459_653, "wine": 5_132_156, "beer": 31_250_000}}
    c = ex.liquor_check()
    # Gallons times DOTAX's rates land within 3% of what DOTAX collected.
    assert c["implied"] / c["collected"] == pytest.approx(1.0, abs=0.03)


def test_score_liquor():
    gallons = ex.load_bases()["liquor_gallons"]
    year = max(int(y) for y in gallons)
    s = ex.score_liquor({"beer": ex.LIQUOR_RATES["beer"] + 0.10}, year + 1)
    assert s.state == pytest.approx(0.10 * gallons[str(year)]["beer"] / 1e3)
    every = ex.score_liquor({k: r * 1.1 for k, r in ex.LIQUOR_RATES.items()}, year + 1)
    assert every.state == pytest.approx(0.1 * load_ledger()[year + 1].lines["liquor"], rel=0.03)
    with pytest.raises(KeyError):
        ex.score_liquor({"sake": 2.0}, year + 1)
