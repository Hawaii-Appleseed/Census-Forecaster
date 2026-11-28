"""Unemployment contributions: DOL data, the schedule model and its place in the baseline."""
import pytest
from state_revenue import load_ledger
from state_revenue import unemployment as un
from state_revenue.scripts.refresh_ui import parse_204, parse_2112, parse_dlir

pytestmark = pytest.mark.smoke


def test_parsers():
    m = parse_2112('"st","rptdate","c1","c3","c9","c13","c51"\n"HI","2025-10-31",1,800.5,42.8,5.5,12.25\n'
                   '"AK","2025-10-31",1,2,3,4,5\n')
    assert m == {"2025-10": {"contributions": 42.8, "trust_fund_balance": 800.5, "benefits": 12.25,
                             "interest": 5.5}}
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


def test_statutory_rule_reproduces_announced_schedules():
    from state_revenue import ui_schedule as us
    assert [us.schedule_for_ratio(r) for r in (1.70, 1.694, 1.30, 1.294, 1.0, 0.8, 0.43, 0.2, 0.19)] == [
        "A", "B", "B", "C", "C", "D", "F", "G", "H"]
    rows = {r["for_year"]: r for r in us.check()}
    assert set(rows) == {2020, 2021, 2022, 2023, 2024, 2025, 2026}
    for year in (2023, 2024, 2025, 2026):                       # set by the ratio, and reproduced
        assert rows[year]["match"] and not rows[year]["statutory"], year
    assert rows[2023]["ratio"] == pytest.approx(0.43, abs=0.01)
    assert rows[2021]["statutory"] and rows[2021]["ratio"] < 0.05     # the statute overrode schedule H
    # The one miss: 1.37 where DLIR's own figures must have been under 1.30.
    assert not rows[2020]["match"] and rows[2020]["ratio"] == pytest.approx(1.37, abs=0.02)


def test_projected_schedules_feed_the_forecast():
    from state_revenue import ui_schedule as us
    ui = un.load_ui()
    sched = us.projected_schedules(2031)
    assert {y: sched[y] for y in ui.schedules} == ui.schedules     # announced years are kept
    ratios = us.projected_ratios(2031)
    assert set(ratios) == set(range(max(ui.schedules) + 1, 2032))
    assert all(sched[y] == us.schedule_for_ratio(ratios[y]) for y in ratios)
    # a schedule never used takes the last rate moved by the statute's new-employer rates
    last = max(un.effective_rates(ui))
    fc = un.forecast_rate_year(last + 2, ui, schedule="B")
    same = un.forecast_rate_year(last + 2, ui, schedule=ui.schedules[last])
    assert fc / same == pytest.approx(un.NEW_EMPLOYER_RATE["B"] / un.NEW_EMPLOYER_RATE[ui.schedules[last]])
