"""Unemployment contributions: DOL data, the schedule model and its place in the baseline."""
import pytest
from state_revenue import load_ledger
from state_revenue import unemployment as un
from state_revenue.scripts.refresh_ui import parse_204, parse_2112, parse_dlir

pytestmark = pytest.mark.smoke


def test_parsers():
    m = parse_2112('"st","rptdate","c1","c3","c9"\n"HI","2025-10-31",1,800.5,42.8\n"AK","2025-10-31",1,2,3\n')
    assert m == {"2025-10": {"contributions": 42.8, "trust_fund_balance": 800.5}}
    p = parse_204('"st","rptdate","c5","c7","c8"\n"HI","2026-12-31",2025-12-31,31.0,21.9\n'
                  '"HI","1990-12-31",1990-06-30,9,8\n')
    assert p == {2025: {"total_payroll": 31.0, "taxable_payroll": 21.9}}
    page = ("<table><tr><td>Calendar Year</td><td>2026</td><td>2025</td></tr>"
            "<tr><td>Contribution Rate Schedule</td><td>C</td><td>D</td></tr>"
            "<tr><td>Taxable Wage Base (per employee)</td><td>$64,500</td><td>$62,000</td></tr>"
            "<tr><td>Tax Rate for New Employers</td><td>2.40%</td><td>3.00%</td></tr>"
            "<tr><td>Maximum Tax Rate</td><td>5.60%</td><td>5.80%</td></tr></table>")
    d = parse_dlir(page)
    assert d[2026] == {"schedule": "C", "wage_base": 64500.0, "new_employer_rate": 0.024,
                       "max_rate": 0.056}
    assert d[2025]["schedule"] == "D"
    with pytest.raises(ValueError):
        parse_dlir("<p>moved</p>")


def test_effective_rates_follow_the_schedule():
    ui = un.load_ui()
    rates = un.effective_rates(ui)
    by = {}
    for year, schedule in ui.schedules.items():
        if year in rates:
            by.setdefault(schedule, []).append(rates[year])
    assert max(by["C"]) < min(by["D"]) < max(by["D"]) < min(by["F"])
    assert rates[2023] == pytest.approx(0.0252, abs=0.0005)        # schedule F
    assert ui.rate_year(2023) == pytest.approx(505.0e6, rel=0.001)


def test_dotax_line_changed_coverage():
    ui, ledger = un.load_ui(), load_ledger()
    ratio = {fy: ledger[fy].lines["unemployment"] * 1e3 / ui.fiscal_year(fy) for fy in range(2016, 2026)}
    assert all(r < 0.75 for fy, r in ratio.items() if fy <= 2023)
    assert ratio[2025] == pytest.approx(1.07, abs=0.01)
    assert un.dotax_ratio(ui) == (2025, pytest.approx(ratio[2025]))


def test_backtest_and_forecast():
    rows = {b["rate_year"]: b for b in un.backtest()}
    assert set(rows) == {2020, 2021, 2022, 2023, 2024, 2025}
    assert abs(rows[2022]["error"]) < 0.02 and abs(rows[2024]["error"]) < 0.03
    assert rows[2023]["schedule_seen_before"] is False             # the first F year
    mean = sum(abs(b["error"]) for b in rows.values()) / len(rows)
    assert mean == pytest.approx(0.128, abs=0.01)
    ui = un.load_ui()
    # A fiscal year DOL has fully reported is returned as reported.
    assert un.forecast_fiscal_year(2026) == pytest.approx(ui.fiscal_year(2026))
    assert un.forecast_fiscal_year(2026, known_through=2024) != pytest.approx(ui.fiscal_year(2026))
    later = un.forecast_fiscal_year(2028)
    assert 0.8 * ui.fiscal_year(2026) < later < 1.4 * ui.fiscal_year(2026)
    assert un.ledger_line(2028) == pytest.approx(later * un.dotax_ratio(ui)[1] / 1e3)
