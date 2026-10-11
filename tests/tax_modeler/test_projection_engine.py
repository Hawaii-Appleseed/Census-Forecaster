"""``projection.engine``: the one aging engine, its top-share drift, and the
pieces the top-tail work of October 2026 hung on it (the synthetic-row fix in
the CBO decomposition, the windowed gains anchor, the realization null, the
anticipatory-realization hook)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration import cg_anchor, cg_realization, dotax_base
from tax_modeler.calibration.cbo_aging import (
    _component_amounts_for_filer,
    age_filers_with_components,
)
from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR
from tax_modeler.projection import engine
from tax_modeler.projection.engine import TOP_SHARE_DRIFT, AgingSpec

# ── the spec ────────────────────────────────────────────────────────────────

def test_default_spec_is_cbo_with_no_drift():
    s = AgingSpec()
    assert (s.engine, s.top_share_drift, s.drift_threshold, s.drift_base_year) == (
        "cbo", 0.0, 500_000.0, PUMS_INCOME_DOLLAR_YEAR)


def test_scenario_drifts_are_a_band_around_zero():
    assert TOP_SHARE_DRIFT == {"LOW": -0.010, "MID": 0.0, "HIGH": 0.010}
    assert AgingSpec.for_scenario("high").top_share_drift == 0.010
    assert AgingSpec.for_scenario("MID", engine="scalar").engine == "scalar"


def test_unknown_engine_rejected():
    with pytest.raises(ValueError, match="engine"):
        AgingSpec(engine="b19013")


def _units(n=200, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    inc = rng.lognormal(11.0, 0.9, n)
    inc[:5] = [600_000, 900_000, 1.5e6, 3e6, 8e6]
    df = pd.DataFrame({
        "income": inc, "weight": rng.uniform(5, 40, n),
        "filing_status": rng.choice(["single", "married_filing_jointly", "head_of_household"], n),
        "num_dependents": rng.integers(0, 3, n),
        "primary_wagp": inc * 0.8, "primary_intp": inc * 0.05,
        "county": rng.choice(["Honolulu", "Maui", "Hawaii", "Kauai"], n),
    })
    df["agi"] = df["income"]
    return df


def test_cbo_engine_ages_by_component_and_drift_lands_on_top(monkeypatch):
    units = _units()
    base = engine.age_incomes(units, 2027, AgingSpec())
    drifted = engine.project_units(units, 2027, AgingSpec(top_share_drift=0.02))
    top = (base["income"] >= 500_000).to_numpy()
    years = 2027 - PUMS_INCOME_DOLLAR_YEAR
    np.testing.assert_allclose(drifted.loc[top, "income"], base.loc[top, "income"] * 1.02 ** years)
    np.testing.assert_allclose(drifted.loc[~top, "income"], base.loc[~top, "income"])
    # the drifted rows were re-scored (not left blank by the premium step)
    assert not drifted["hi_itemized_deduction"].isna().any()
    np.testing.assert_array_equal(drifted["hi_agi"], drifted["income"])
    # per-source growth: a component-aged frame carries its components
    assert {"cbo_aged_wages", "cbo_aged_capital_gains"} <= set(base.columns)


def test_scalar_engine_is_the_county_median_path():
    units = _units()
    out = engine.project_units(units, 2027, AgingSpec(engine="scalar"))
    assert "cbo_aged_wages" not in out.columns
    assert (out["projection_method"] == "ensemble").all()
    # one factor per county (floating point: equal to rounding)
    ratio = out["income"] / out["income_base_year"]
    spread = ratio.groupby(out["county"]).agg(lambda r: r.max() - r.min())
    assert (spread < 1e-9).all()
    assert ratio.groupby(out["county"]).first().nunique() > 1


def test_component_growth_and_tail_factor():
    g = engine.component_growth(AgingSpec(), 2023, 2024)
    assert g["capital_gains"] > g["wages"] > 1.0
    assert engine.tail_growth_factor(AgingSpec(), 2023, 2024, 0.0) == pytest.approx(g["wages"])
    assert engine.tail_growth_factor(AgingSpec(), 2023, 2023, 0.5) == 1.0
    assert engine.tail_growth_factor(AgingSpec(top_share_drift=0.01), 2023, 2025, 0.0) == pytest.approx(
        engine.component_growth(AgingSpec(), 2023, 2025)["wages"] * 1.01 ** 2)


# ── the synthetic rows in the CBO decomposition ─────────────────────────────

def test_synthetic_rows_do_not_double_count_their_gains():
    """synthesize_top_filers writes the gains into primary_intp AND
    synthetic_cg_share; the decomposition must still sum to income."""
    inc = np.array([2e6, 5e6])
    share = np.array([0.3, 0.5])
    df = pd.DataFrame({
        "income": inc, "weight": [1.0, 1.0], "filing_status": ["single", "married_filing_jointly"],
        "is_synthetic_ultra_high": [True, True], "synthetic_cg_share": share,
        "primary_wagp": inc * (1 - share), "primary_intp": inc * share,
    })
    comps = _component_amounts_for_filer(df)
    np.testing.assert_allclose(sum(comps.values()), inc, rtol=1e-12)
    np.testing.assert_allclose(comps["interest"], 0.0)
    np.testing.assert_allclose(comps["capital_gains"], inc * share)
    aged = age_filers_with_components(df, target_year=2024, base_year=2024)
    np.testing.assert_allclose(aged["income"], inc, rtol=1e-12)
    # a PUMS row with the same columns keeps its interest
    pums = df.assign(is_synthetic_ultra_high=False, synthetic_cg_share=0.0)
    np.testing.assert_allclose(_component_amounts_for_filer(pums)["interest"], inc * share)


def test_imputed_gains_on_pums_rows_are_carved_out_of_the_pums_components():
    """impute_capital_gains_from_soi sets synthetic_cg_share on $100K-$1M PUMS
    rows whose PUMS columns already sum to income (the gains are inside INTP /
    OIP); the share must not be added on top."""
    inc = 900_000.0
    df = pd.DataFrame({
        "income": [inc], "weight": [1.0], "filing_status": ["single"],
        "is_synthetic_ultra_high": [False], "synthetic_cg_share": [0.2],
        "primary_wagp": [inc * 0.7], "primary_intp": [inc * 0.2], "primary_oip": [inc * 0.1],
    })
    comps = _component_amounts_for_filer(df)
    np.testing.assert_allclose(sum(comps.values()), inc, rtol=1e-12)
    assert comps["capital_gains"].iloc[0] == pytest.approx(inc * 0.2)
    # the named non-gains components (0.9 x income; OIP is the residual) give up
    # the 0.1 x income the gains do not fit into, keeping their mix; the residual is 0
    assert comps["wages"].iloc[0] == pytest.approx(inc * 0.7 * (0.8 / 0.9))
    assert comps["interest"].iloc[0] == pytest.approx(inc * 0.2 * (0.8 / 0.9))
    assert comps["other"].iloc[0] == pytest.approx(0.0, abs=1e-6)
    # aging backwards one year cannot push the filer over $1M any more
    aged = age_filers_with_components(df, target_year=2023, base_year=2024)
    assert aged["income"].iloc[0] < inc
    # a row whose PUMS columns sum below income is left alone
    short = df.assign(primary_intp=inc * 0.05, primary_oip=0.0)
    c2 = _component_amounts_for_filer(short)
    assert c2["wages"].iloc[0] == pytest.approx(inc * 0.7) and c2["other"].iloc[0] == pytest.approx(inc * 0.05)
    # only the excess the gains explain is carved: a genuine over-sum stays over
    bad = df.assign(primary_wagp=inc * 1.2)
    assert float(sum(_component_amounts_for_filer(bad).values()).iloc[0]) > inc


# ── the gains anchor: window, growth null, anticipation hook ────────────────

def test_gains_anchor_is_the_window_mean_share_on_the_base_total(monkeypatch):
    monkeypatch.setattr(dotax_base, "GAINS_WINDOW", (2019, 2023))
    monkeypatch.setattr(dotax_base, "BASE_YEAR", 2023)
    totals = dotax_base.load_top_class_series()
    series = cg_anchor.DOTAX_NLTCG_RES
    got = dotax_base.nltcg_anchor_M(2023, series)
    for i in range(6):
        share = np.mean([series[y][i] / totals[str(y)]["total_tax_before_M"] for y in range(2019, 2024)])
        assert got[i] == pytest.approx(share * 2960.0, abs=1e-3)
    assert sum(got) == pytest.approx(3826.1, abs=0.5)        # against $2,646M printed for TY2023
    monkeypatch.setattr(dotax_base, "GAINS_WINDOW", None)
    assert dotax_base.nltcg_anchor_M(2023, series) == series[2023]
    # nonresident gains and other editions are as printed
    monkeypatch.setattr(dotax_base, "GAINS_WINDOW", (2019, 2023))
    assert dotax_base.nltcg_anchor_M(2022, series) == series[2022]
    assert dotax_base.nltcg_anchor_M(2023, series, nonresident=True) == series[2023]


def test_gains_window_needs_every_edition(monkeypatch):
    monkeypatch.setattr(dotax_base, "GAINS_WINDOW", (2015, 2023))
    with pytest.raises(ValueError, match="2015"):
        dotax_base.nltcg_anchor_M(2023, cg_anchor.DOTAX_NLTCG_RES)


def test_gains_growth_models():
    assert cg_anchor.cg_growth(2023, 2023) == 1.0
    window = cg_anchor.cg_growth(2027, 2023, model="window")
    cbo = cg_anchor.cg_growth(2027, 2023, model="cbo")
    assert 1.0 < window < cbo                      # wages x 0.85 grows slower than gains x 0.85
    g = engine.component_growth(AgingSpec(), 2023, 2027)
    assert window == pytest.approx(g["wages"]) and cbo == pytest.approx(g["capital_gains"])
    with pytest.raises(ValueError, match="model"):
        cg_anchor.cg_growth(2027, model="spx")


def test_realization_fit_cannot_beat_the_window_mean():
    r = cg_realization.fit_report()
    assert r.loc[r["spec"] == "window_mean", "n"].item() == 6
    assert not r["supported"].any()
    assert cg_realization.chosen_model(r) == "window_mean"
    # the best candidate's gain is within the noise band the module requires
    assert r["relative_gain"].max() < cg_realization.MIN_RELATIVE_GAIN


def test_anticipatory_realization_is_a_no_op_under_act24():
    for y in range(2026, 2032):
        assert cg_anchor.anticipatory_realization_factor(y) == 1.0
    assert set(cg_anchor.act24_cap_rate_path().values()) == {0.0725}
    # a rise next year pulls realizations forward; a cut does nothing
    rise = {2027: 0.0725, 2028: 0.09}
    assert cg_anchor.anticipatory_realization_factor(2027, rise) == pytest.approx(np.exp(2.0 * 0.0175))
    assert cg_anchor.anticipatory_realization_factor(2028, rise) == 1.0
    assert cg_anchor.anticipatory_realization_factor(2027, {2027: 0.09, 2028: 0.0725}) == 1.0


def test_anchor_targets_use_the_windowed_series(monkeypatch):
    monkeypatch.setattr(cg_anchor, "anchored_nltcg_M", lambda: [1, 2, 3, 4, 5, 60])
    monkeypatch.setattr(cg_anchor, "cg_growth", lambda yr, from_year=2023, model=None: 2.0)
    df = pd.DataFrame({"income": np.linspace(50_000, 3e6, 400), "weight": 10.0,
                       "synthetic_cg_share": 0.1})
    _, _, rows = cg_anchor.anchor_nltcg(df, 2027, 0.8)
    t = {r["group"]: r["dotax_target_M"] for r in rows}
    assert t["100_150"] == 4.0 and t["1mp"] == pytest.approx(120 * 0.8)
