"""The line forecaster, the backtest against the Council and the baseline."""
import math

import pytest
from state_revenue.backtest import origin_meetings, run, run_model_only, summarize
from state_revenue.baseline import build_baseline
from state_revenue.cor import load_cor_forecasts
from state_revenue.forecast import PHI, choose_method, forecast_line, long_run_growth

pytestmark = pytest.mark.smoke


def _grow(start, rates, first=2000):
    out, v = {first: start}, start
    for i, r in enumerate(rates, 1):
        v *= math.exp(r)
        out[first + i] = v
    return out


def test_steady_growth_is_carried_forward():
    h = _grow(100.0, [0.04] * 15)
    fc = forecast_line(h, 3)
    assert fc.method == "damped"
    assert fc.values[2016] == pytest.approx(h[2015] * math.exp(0.04))
    assert fc.values[2018] == pytest.approx(h[2015] * math.exp(0.12))


def test_damping_pulls_recent_growth_to_long_run():
    h = _grow(100.0, [0.03] * 12 + [0.10, 0.10, 0.10])
    fc = forecast_line(h, 30)
    assert fc.start_growth > fc.long_run_growth == pytest.approx(0.03)
    first = math.log(fc.values[2016] / h[2015])
    late = math.log(fc.values[2045] / fc.values[2044])
    assert first == pytest.approx(0.03 + (fc.start_growth - 0.03) * PHI)
    assert late == pytest.approx(0.03, abs=1e-3)


def test_breaks_stay_out_of_growth():
    h = _grow(100.0, [0.04] * 10 + [-0.8, 0.9] + [0.04] * 3)
    assert forecast_line(h, 1).long_run_growth == pytest.approx(0.04)


def test_noisy_lines_use_the_level_method():
    h = {2000 + i: v for i, v in enumerate([10, 30, 8, 25, 12, 40, 9, 22, 15, 35, 11])}
    assert choose_method(h) == "level"
    fc = forecast_line(h, 2)
    assert fc.method == "level"
    # The median of the last five years (9, 22, 15, 35, 11); every swing is a
    # break, so there is no long-run growth to add.
    assert fc.values[2011] == pytest.approx(15.0)


def test_short_history_takes_the_fallback_growth():
    h = _grow(100.0, [0.20] * 5)
    assert long_run_growth(h) is None
    fc = forecast_line(h, 1, fallback_growth=0.03)
    assert fc.long_run_growth == 0.03


def test_one_origin_meeting_a_year():
    meetings = origin_meetings(load_cor_forecasts())
    assert min(meetings) == 2014
    assert all(7 <= f.meeting.month <= 9 for f in meetings.values())


def test_backtest_gate():
    """The figures STATE_REVENUE_SCOPE.md reports (excluding FY2020-FY2022)."""
    s = {(x.line, x.horizon): x for x in summarize(run())}
    assert s[("total", 1)].n == 8 and s[("total", 2)].n == 7
    assert s[("total", 1)].model_mape == pytest.approx(0.029, abs=0.002)
    assert s[("total", 1)].cor_mape == pytest.approx(0.029, abs=0.002)
    assert s[("total", 2)].model_mape == pytest.approx(0.047, abs=0.002)
    assert s[("total", 2)].cor_mape == pytest.approx(0.049, abs=0.002)
    assert s[("transient_accommodations", 1)].ratio > 2        # the law splits it: COR wins
    assert s[("individual_income", 2)].ratio > 1.2             # enacted rate changes: COR wins
    assert s[("insurance_premiums", 1)].ratio < 1              # a smooth line: the model wins


def test_model_only_backtest_covers_its_lines():
    lines = {m.line for m in run_model_only()}
    assert {"fuel", "motor_vehicle", "unemployment", "county_surcharge"} <= lines


def test_general_fund_baseline_is_the_councils():
    cor = load_cor_forecasts()[-1]
    b = build_baseline("general_fund")
    assert b.cor_meeting == cor.meeting
    for fy in b.fiscal_years:
        expected = cor.estimates.get(fy) or cor.base[fy]
        assert b.total(fy) == pytest.approx(expected["total"], abs=5)
        assert {v.source for v in b.values[fy].values()} == {"cor"}


def test_all_funds_baseline_sources():
    b = build_baseline("all")
    row = b.values[b.fiscal_years[0]]
    assert row["general_excise"].source == "cor_scaled"
    assert row["transient_accommodations"].source == "cor_scaled"
    assert row["county_surcharge"].source == "rides_on"
    assert row["fuel"].source == "model" and row["unemployment"].source == "model"
    # All funds hold more than the general fund's part.
    gf = build_baseline("general_fund")
    for fy in b.fiscal_years:
        assert b.total(fy) > gf.total(fy)
        assert b.line("transient_accommodations")[fy] > gf.line("transient_accommodations")[fy]


def test_model_baseline_uses_no_council_figures():
    b = build_baseline("all", source="model")
    assert b.cor_meeting is None
    assert {v.source for row in b.values.values() for v in row.values()} <= {"model", "rides_on"}
    # Rental vehicles have six years of growth: they take the total's long-run rate.
    rv = b.line("rental_vehicle")
    years = sorted(rv)
    assert math.log(rv[years[-1]] / rv[years[-2]]) < 0.08
