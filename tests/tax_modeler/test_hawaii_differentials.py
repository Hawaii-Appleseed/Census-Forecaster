"""Hawaii differentials: estimator, additive/ratio growth, QCEW wages, backtest gate."""
import math

import pandas as pd
import pytest

from tax_modeler.calibration import hawaii_differentials as hd
from tax_modeler.calibration.cbo_aging import (
    DEFAULT_HAWAII_FACTORS,
    HawaiiFactors,
    hawaii_growth,
    load_cbo_rates,
    net_growth_factor,
)
from tax_modeler.projection.engine import AgingSpec


@pytest.fixture(scope="module")
def rates():
    return load_cbo_rates("2025-01")


def test_weighted_estimate_matches_hand_computation():
    d = pd.Series([0.0, 0.03], index=[2021, 2022])
    e = hd.weighted_estimate(d)
    w = [0.5 ** (1 / 3), 1.0]
    assert e.d == pytest.approx(0.03 / sum(w))
    assert e.n == 2 and 1.9 < e.n_eff <= 2.0 and e.se > 0


def test_single_point_has_no_se():
    assert math.isnan(hd.weighted_estimate(pd.Series([0.02], index=[2022])).se)


def test_growth_table_uses_hawaii_table21_for_gains():
    t = hd.growth_table("capital_gains")
    assert list(t.index) == [2019, 2020, 2021, 2022, 2023]


def test_ratio_growth_reproduces_old_rule_exactly(rates):
    for c in DEFAULT_HAWAII_FACTORS:
        old = net_growth_factor(rates.growth(c, 2029, 2024), DEFAULT_HAWAII_FACTORS[c])
        assert hawaii_growth(dict(DEFAULT_HAWAII_FACTORS), c, rates, 2024, 2029) == pytest.approx(old)


def test_additive_is_cbo_log_growth_plus_d(rates):
    hf = HawaiiFactors({"interest": -0.02})
    want = math.exp(math.log(rates.growth("interest", 2029, 2024)) - 0.02 * 5)
    assert hawaii_growth(hf, "interest", rates, 2024, 2029) == pytest.approx(want)
    # a component with no d_hat keeps its ratio
    assert hawaii_growth(hf, "other", rates, 2024, 2029) == pytest.approx(
        net_growth_factor(rates.growth("other", 2029, 2024), DEFAULT_HAWAII_FACTORS["other"]))


def test_realized_wages_replace_cbo_then_hand_off(rates):
    hf = HawaiiFactors({"wages": 0.0}, realized={"wages": {2025: 1.10}})
    want = 1.10 * rates.growth("wages", 2027, 2025)
    assert hawaii_growth(hf, "wages", rates, 2024, 2027) == pytest.approx(want)
    # ratio mode hands off to the old ratio, not to a compounded per-year ratio
    r = HawaiiFactors({}, realized={"wages": {2025: 1.10}})
    assert hawaii_growth(r, "wages", rates, 2024, 2027) == pytest.approx(
        1.10 * net_growth_factor(rates.growth("wages", 2027, 2025), 0.85))


def test_qcew_total_wages_complete_years_and_growth():
    s = hd.qcew_total_wages()
    assert s.index.max() >= 2025
    g = hd.qcew_wage_growth()
    assert g[2020] < 1.0 < g[2021]          # the pandemic dip and rebound


def test_spec_modes_and_validation():
    assert isinstance(AgingSpec(hawaii_mode="additive").resolved_hawaii_factors(), HawaiiFactors)
    with pytest.raises(ValueError):
        AgingSpec(hawaii_mode="nope")


def test_shift_moves_every_component_by_its_se():
    a, b = hd.build_factors("additive"), hd.build_factors("additive", shift_se=1.645)
    for c in ("wages", "retirement", "dividends"):
        assert b.d_hat[c] - a.d_hat[c] == pytest.approx(1.645 * a.se[c])


def test_backtest_gate_logic_and_data():
    bt = hd.backtest()
    assert set(bt["component"]) == set(hd.COMPONENTS)
    cg = bt[bt.component == "capital_gains"].set_index("year")
    assert math.isnan(cg.loc[2019, "err_additive"]) and cg.loc[2020, "n_hist"] == 1   # TY2019 has no history
    sc = hd.score(bt)
    assert sc.iloc[-1]["component"] == "POOLED"
    ok, _ = hd.verdict(sc)
    assert isinstance(ok, bool)
    # a synthetic pass: additive better everywhere
    good = sc.copy()
    good["rmse_additive"], good["rmse_change"] = good["rmse_ratio"] * 0.9, -0.1
    assert hd.verdict(good)[0]
    bad = good.copy()
    bad.loc[0, "rmse_change"] = 0.2
    assert not hd.verdict(bad)[0]


def test_qcew_tracks_soi_hawaii_wages_better_than_us_plus_d():
    q = hd.qcew_check()
    rm = lambda c: ((q[c] - q["soi_hi"]) ** 2).mean() ** 0.5
    assert rm("qcew") < rm("us_plus_d")


def test_backward_window_with_realized_is_the_reciprocal(rates):
    hf = HawaiiFactors({"wages": -0.01}, realized={"wages": {2025: 1.10}})
    fwd = hawaii_growth(hf, "wages", rates, 2024, 2027)
    assert hawaii_growth(hf, "wages", rates, 2027, 2024) == pytest.approx(1.0 / fwd)


def test_env_selects_the_default_mode(monkeypatch):
    monkeypatch.setenv("HAWAII_AGING_MODE", "additive")
    monkeypatch.setenv("HAWAII_AGING_SHIFT_SE", "1.645")
    spec = AgingSpec()
    assert spec.hawaii_mode == "additive" and spec.hawaii_shift_se == 1.645


def test_empty_growth_shift_reproduces_current_run_exactly(rates, monkeypatch):
    monkeypatch.delenv("HAWAII_GROWTH_SHIFT", raising=False)
    spec = AgingSpec()
    assert not spec.growth_shift
    base = spec.resolved_hawaii_factors()
    for explicit in (AgingSpec(growth_shift={}), AgingSpec(growth_shift={"wages": 0.0})):
        hf = explicit.resolved_hawaii_factors()
        for c in ("wages", "business", "capital_gains", "dividends", "interest", "retirement", "other"):
            assert hawaii_growth(hf, c, rates, 2023, 2031) == hawaii_growth(base, c, rates, 2023, 2031)


def test_growth_shift_adds_log_points_per_projected_year_only(rates):
    for mode in ("ratio", "additive"):
        b = AgingSpec(hawaii_mode=mode).resolved_hawaii_factors()
        s = AgingSpec(hawaii_mode=mode, growth_shift={"wages": 0.02, "business": 0.01}).resolved_hawaii_factors()
        # wages: QCEW realized through 2025, so only 2025 -> 2028 is projected
        assert (hawaii_growth(s, "wages", rates, 2023, 2028) / hawaii_growth(b, "wages", rates, 2023, 2028)
                == pytest.approx(math.exp(0.02 * 3)))
        assert (hawaii_growth(s, "wages", rates, 2023, 2025) == hawaii_growth(b, "wages", rates, 2023, 2025))
        assert (hawaii_growth(s, "business", rates, 2023, 2028) / hawaii_growth(b, "business", rates, 2023, 2028)
                == pytest.approx(math.exp(0.01 * 5)))
        assert (hawaii_growth(s, "proprietors", rates, 2023, 2028) / hawaii_growth(b, "proprietors", rates, 2023, 2028)
                == pytest.approx(math.exp(0.01 * 5)))
        assert hawaii_growth(s, "interest", rates, 2023, 2028) == hawaii_growth(b, "interest", rates, 2023, 2028)


def test_growth_shift_env_is_the_spec_default(monkeypatch):
    monkeypatch.setenv("HAWAII_GROWTH_SHIFT", '{"dividends": 0.05}')
    assert dict(AgingSpec().growth_shift) == {"dividends": 0.05}
