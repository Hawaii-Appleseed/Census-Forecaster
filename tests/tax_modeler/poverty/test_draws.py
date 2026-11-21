"""Poverty headcounts from draws of who claims each credit (tax_modeler.poverty.draws), not an expected credit for
everyone."""
from __future__ import annotations

import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from tax_modeler.poverty.draws import ClaimBlock, DrawSpec, poverty_draws
from tax_modeler.poverty.impact import TakeupRates, compute_poverty_impact

SCRIPT = Path(__file__).resolve().parents[3] / "chain/forecast_working_family_credits.py"


def _frame(resources, ids=None):
    n = len(resources)
    return pd.DataFrame({"spm_unit_id": ids if ids is not None else np.arange(n), "spm_resources": resources})


def _spec(blocks, fixed, scenario="s", baseline=True, n=4000, seed=3):
    return DrawSpec(blocks=tuple(blocks), baseline_fixed=(lambda df: np.asarray(fixed)) if baseline else None,
                    scenario_fixed={scenario: lambda df: np.asarray(fixed)}, n_draws=n, seed=seed)


def test_one_claim_gives_the_analytic_probability_and_the_claim_is_common_to_both_cases():
    # the line is 1,000; the unit has 950 without the credit and receives 100 if it claims (30% do); the credit
    # is worth nothing in the scenario
    df = _frame([950.0])
    blk = ClaimBlock(np.array([0]), np.array([0.3]), np.array([100.0]), {"s": np.array([0.0])})
    res = poverty_draws(_spec([blk], [950.0]), df, np.array([1_000.0]), {"persons": np.array([3.0])})
    assert res.poor_prob["baseline"][0] == pytest.approx(0.7, abs=0.03)
    assert res.poor_prob["s"][0] == 1.0
    lifted = res.counts[("persons", "s")] - res.counts[("persons", "baseline")]
    assert lifted.mean() == pytest.approx(3 * 0.3, abs=0.1)
    # every draw: poor in the scenario, and in the baseline exactly when it did not claim (a common claim)
    assert set(np.unique(res.counts[("persons", "baseline")])) == {0.0, 3.0}


def test_two_claimants_in_one_spm_unit_draw_independently():
    df = _frame([0.0], ids=[7])
    ids = np.array([7, 7])
    blk = ClaimBlock(ids, np.array([0.5, 0.5]), np.array([60.0, 60.0]), {"s": np.zeros(2)})
    res = poverty_draws(_spec([blk], [0.0], n=6000), df, np.array([100.0]), {"persons": np.array([1.0])})
    # resources 120 (both claim) clear the line of 100; 60 or 0 do not: not poor with probability 1/4
    assert res.poor_prob["baseline"][0] == pytest.approx(0.75, abs=0.02)


def test_the_expectation_can_sit_on_the_wrong_side_of_the_line():
    """Giving the unit its expected credit (30) puts it above a line of 25 every time; drawn, it is poor 70% of the time."""
    df = _frame([0.0])
    blk = ClaimBlock(np.array([0]), np.array([0.3]), np.array([100.0]), {"s": np.array([0.0])})
    res = poverty_draws(_spec([blk], [0.0]), df, np.array([25.0]), {"persons": np.array([1.0])})
    assert 0.3 * 100.0 > 25.0 and res.poor_prob["baseline"][0] == pytest.approx(0.7, abs=0.03)


def test_the_seed_fixes_the_draws_and_a_scenario_only_spec_leaves_the_baseline_alone():
    df = _frame([950.0, 990.0])
    blk = ClaimBlock(np.array([0, 1]), np.array([0.5, 0.2]), np.array([100.0, 100.0]), {"s": np.zeros(2)})
    a = poverty_draws(_spec([blk], [950.0, 990.0], seed=11), df, np.array([1_000.0, 1_000.0]), {"persons": np.ones(2)})
    b = poverty_draws(_spec([blk], [950.0, 990.0], seed=11), df, np.array([1_000.0, 1_000.0]), {"persons": np.ones(2)})
    c = poverty_draws(_spec([blk], [950.0, 990.0], seed=12), df, np.array([1_000.0, 1_000.0]), {"persons": np.ones(2)})
    np.testing.assert_array_equal(a.counts[("persons", "s")], b.counts[("persons", "s")])
    assert not np.array_equal(a.counts[("persons", "baseline")], c.counts[("persons", "baseline")])
    only = poverty_draws(_spec([blk], [950.0, 990.0], baseline=False), df, np.array([1_000.0, 1_000.0]), {"persons": np.ones(2)})
    assert "baseline" not in only.poor_prob and "s" in only.poor_prob


def _units(rng, n=60):
    rows = []
    for i in range(n):
        kids = int(rng.integers(0, 4))
        inc = float(rng.uniform(8_000, 30_000))
        rows.append({"filing_status": "head_of_household", "num_dependents": kids, "num_qualifying_children": kids,
                     "total_cash_income": inc, "earned_income": inc, "income": inc, "weight": 100.0,
                     "hi_tax_liability": 0.0, "eitc_amount": 0.0, "ctc_total": 0.0, "ctc_refundable": kids * 1_500.0,
                     "hi_eitc_amount": 0.0, "tenure": "renter", "county": "Honolulu", "house_district": 1,
                     "senate_district": 1, "PUMA": 301, "spm_unit_id": i})
    from tax_modeler.credits.arpa_ctc import arpa_ctc_for_tax_units
    return arpa_ctc_for_tax_units(pd.DataFrame(rows))


def test_an_expansion_scenario_counts_claimants_not_a_fraction_of_everyone():
    units = _units(np.random.default_rng(5))
    rate = 0.7
    drawn = compute_poverty_impact(units, tax_year=2024, scenarios=("hi_ctc_650",), takeup=TakeupRates(hi_ctc=rate))
    expected = compute_poverty_impact(units, tax_year=2024, scenarios=("hi_ctc_650",), takeup=TakeupRates(hi_ctc=rate),
                                      claim_draws=False)
    df = drawn.units
    thr, r = df["spm_threshold"].to_numpy(), df["spm_resources"].to_numpy()
    kids = df["num_qualifying_children"].to_numpy(dtype=float)
    pw = df["weight"].to_numpy() * (1 + (df["filing_status"] == "married_filing_jointly").to_numpy() + df["num_dependents"].to_numpy())
    want = rate * (r + 650.0 * kids < thr) + (1 - rate) * (r < thr)
    np.testing.assert_allclose(df["poor_prob_hi_ctc_650"].to_numpy(), want)
    s = drawn.by_state.iloc[0]
    assert s["persons_lifted_hi_ctc_650"] == pytest.approx(float(pw @ ((r < thr) - want)))
    # the gap is the expectation of the gap over who claims; the gap of the expected resources is kept beside it
    fw = df["weight"].to_numpy()
    gap_scn = rate * np.maximum(thr - (r + 650.0 * kids), 0.0) + (1 - rate) * np.maximum(thr - r, 0.0)
    assert s["gap_closed_hi_ctc_650_$"] == pytest.approx(float(fw @ (np.maximum(thr - r, 0.0) - gap_scn)))
    e = expected.by_state.iloc[0]["gap_closed_hi_ctc_650_$"]
    assert s["gap_closed_hi_ctc_650_expected_$"] == pytest.approx(e)
    assert s["gap_closed_hi_ctc_650_$"] <= e + 1e-6                     # convex: the expected-resources gap overstates the closing
    assert "gap_closed_hi_ctc_650_expected_$" not in expected.by_state.columns
    assert "poor_prob_hi_ctc_650" not in expected.units.columns
    # at take-up 1 and 0 the two agree
    for r_ in (0.0, 1.0):
        a = compute_poverty_impact(units, tax_year=2024, scenarios=("hi_ctc_650",), takeup=TakeupRates(hi_ctc=r_))
        b = compute_poverty_impact(units, tax_year=2024, scenarios=("hi_ctc_650",), takeup=TakeupRates(hi_ctc=r_),
                                   claim_draws=False)
        assert a.by_state.iloc[0]["persons_lifted_hi_ctc_650"] == pytest.approx(b.by_state.iloc[0]["persons_lifted_hi_ctc_650"])


def test_draws_through_compute_poverty_impact_report_the_monte_carlo_error():
    units = _units(np.random.default_rng(8))
    pre = compute_poverty_impact(units, tax_year=2024, scenarios=("no_hi_eitc",))
    base = pre.units["spm_resources"].to_numpy()
    n = len(units)
    blk = ClaimBlock(units["spm_unit_id"].to_numpy(), np.full(n, 0.5), np.full(n, 400.0), {"no_hi_eitc": np.zeros(n)})
    spec = DrawSpec(blocks=(blk,), baseline_fixed=lambda df: df["spm_resources"].to_numpy() - 200.0,
                    scenario_fixed={"no_hi_eitc": lambda df: df["spm_resources"].to_numpy() - 200.0}, n_draws=800, seed=1)
    res = compute_poverty_impact(units, tax_year=2024, scenarios=("no_hi_eitc",), draws=spec)
    info = res.draw_info["no_hi_eitc"]
    assert info["n_draws"] == 800 and info["persons_lifted_mc_se"] >= 0
    assert info["persons_lifted_mean"] == pytest.approx(res.by_state.iloc[0]["persons_lifted_no_hi_eitc"], rel=1e-9)
    assert "poor_prob_baseline" in res.units.columns and np.isfinite(base).all()
    # the gap is the mean over the draws, which is at least the gap of the expected resources (convexity)
    s = res.by_state.iloc[0]
    assert "gap_closed_no_hi_eitc_expected_$" in res.by_state.columns
    assert info["gap_closed_draw_$"] == pytest.approx(s["gap_closed_no_hi_eitc_$"], rel=1e-9)
    assert info["gap_closed_expectation_$"] == pytest.approx(s["gap_closed_no_hi_eitc_expected_$"], rel=1e-9)


@pytest.fixture(scope="module")
def wfc():
    tree = ast.parse(SCRIPT.read_text())
    wanted = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "claim_draws"]
    ns: dict = {"np": np, "pd": pd, "DRAW_COUNT": 100}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), str(SCRIPT), "exec"), ns)
    return ns


def test_the_working_family_draws_keep_every_units_expected_credit(wfc):
    n = 6
    u = pd.DataFrame({"spm_unit_id": np.arange(n)})
    c = pd.DataFrame({
        "eitc_expanded": [0, 900.0, 1500, 0, 300, 2500], "eitc_reverted": [0, 450.0, 750, 0, 150, 1250],
        "food_expanded": [100.0, 0, 250, 80, 0, 300], "food_reverted": [40.0, 0, 100, 30, 0, 120],
        "cdcc_expanded": [0, 0, 200.0, 0, 50, 0], "cdcc_reverted": [0, 0, 80.0, 0, 20, 0],
        "cdcc_expanded_claim_prob": [0, 0, 0.1, 0, 0.05, 0]})
    p_eitc, p_food, p_cdcc = 0.978, 0.732, 0.763
    spec = wfc["claim_draws"](u, c, p_eitc, p_food, p_cdcc)
    e, f, cd = spec.blocks
    np.testing.assert_allclose(e.prob * e.renewed, c["eitc_expanded"] * p_eitc)
    np.testing.assert_allclose(e.prob * e.scenario["act163_sunset"], c["eitc_reverted"] * p_eitc)
    np.testing.assert_allclose(f.prob * f.renewed, c["food_expanded"] * p_food)
    np.testing.assert_allclose(cd.prob * cd.renewed, c["cdcc_expanded"] * p_cdcc)
    np.testing.assert_allclose(cd.prob * cd.scenario["act163_sunset"], c["cdcc_reverted"] * p_cdcc)
    # a take-up factor above one is a dollar scale: claim probability one, dollars scaled
    spec2 = wfc["claim_draws"](u, c, 1.05, p_food, p_cdcc)
    np.testing.assert_allclose(spec2.blocks[0].prob[c["eitc_expanded"] > 0], 1.0)
    np.testing.assert_allclose(spec2.blocks[0].prob * spec2.blocks[0].renewed, c["eitc_expanded"] * 1.05)
    assert spec.n_draws == 100 and set(spec.scenario_fixed) == {"act163_sunset"}
