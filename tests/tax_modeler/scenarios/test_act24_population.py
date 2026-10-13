"""``act24_population.build_units``: the synthetic $1M+ tail is calibrated to
DOTAX's $1M+ tax until re-scoring lands on it, then aged to the PUMS income
dollar year by the scenario's aging spec (the engine and its top-share drift;
audit of September 29, 2026, engine of October 2026; see
test_top_income_synthesis.py)."""
from __future__ import annotations

import numpy as np
import pytest
import tax_modeler.calibration.cg_anchor as cg_anchor
import tax_modeler.calibration.cg_imputation as cg_imputation
from tax_modeler.projection.engine import AgingSpec
from tax_modeler.scenarios import act24_population, top_income_synthesis
from tax_modeler.scenarios.act24_population import build_units
from tax_modeler.scenarios.top_income_synthesis import (
    DOTAX_1M_PLUS_TAX_TARGET_M,
    age_synthetic_tail,
    calibrate_synthetic_tail_to_tax_target,
    synthetic_tail_aging_factor,
)

CAL_TAX_YEAR = 2023
INCOME_COLS = ["income", "agi", "synthetic_total_income", "earned_income",
               "investment_income", "primary_wagp", "primary_intp"]


def test_spec_is_required(tail_units, ded_params):
    with pytest.raises(TypeError, match="spec"):
        build_units(tail_units, alpha=1.5, ded_params=ded_params, cal_tax_year=CAL_TAX_YEAR)


@pytest.fixture
def no_synthesis(monkeypatch):
    """build_units on a frame that already carries its tail: synthesis and the
    capital-gains imputation (which need the calibrated base) pass it through."""
    monkeypatch.setattr(top_income_synthesis, "synthesize_top_filers",
                        lambda df, pareto_alpha: df.copy())
    monkeypatch.setattr(cg_imputation, "impute_capital_gains_from_soi", lambda df: df)


@pytest.mark.parametrize("spec", [AgingSpec(top_share_drift=-0.01), AgingSpec(),
                                  AgingSpec(top_share_drift=0.01), AgingSpec(engine="scalar")],
                         ids=["low", "mid", "high", "scalar"])
def test_tail_calibrated_to_the_dotax_year_then_aged(no_synthesis, tail_units, ded_params, score,
                                             spec):
    units, tail_k = build_units(tail_units, alpha=1.5, spec=spec,
                                ded_params=ded_params, cal_tax_year=CAL_TAX_YEAR)

    # tail_k is fit on the anchored tail: its gains set to the $1M+ Table 21 target first,
    # and tail_k then scales the ordinary income alone
    anchored, _ = cg_anchor.anchor_tail_gains(tail_units)
    calibrated, k = calibrate_synthetic_tail_to_tax_target(score(anchored), score=score,
                                                           scale_gains=False)
    assert tail_k == k
    m = tail_units["is_synthetic_ultra_high"].to_numpy(dtype=bool)
    tax = lambda df: float((df.loc[m, "hi_tax_liability"] * df.loc[m, "weight"]).sum() / 1e6)  # noqa: E731
    assert tax(calibrated) == pytest.approx(DOTAX_1M_PLUS_TAX_TARGET_M, rel=1e-3)

    g = np.array([synthetic_tail_aging_factor(spec, cg_share=s)
                  for s in calibrated.loc[m, "synthetic_cg_share"]])
    for col in INCOME_COLS:
        np.testing.assert_allclose(units.loc[m, col], calibrated.loc[m, col] * g,
                                   rtol=1e-12, err_msg=col)
        np.testing.assert_array_equal(units.loc[~m, col], tail_units.loc[~m, col], err_msg=col)
    # Re-scored on the calibration basis at the aged incomes.
    aged = score(age_synthetic_tail(calibrated, spec=spec))
    np.testing.assert_allclose(units["hi_tax_liability"], aged["hi_tax_liability"])
    assert tax(units) > DOTAX_1M_PLUS_TAX_TARGET_M * g.min() * 0.99


def test_mid_gains_factors_builds_with_the_spec(monkeypatch, tail_units):
    seen = {}

    def fake_build_units(base, **kw):
        seen.update(kw)
        return base, 1.0

    monkeypatch.setattr(act24_population, "build_units", fake_build_units)
    monkeypatch.setattr(act24_population, "project_units", lambda u, **kw: u)
    monkeypatch.setattr(cg_anchor, "anchor_factors", lambda df, y: {"400p": 1.0})
    spec = AgingSpec(top_share_drift=0.01)
    out = act24_population.mid_gains_factors(tail_units, alpha=1.5, spec=spec,
                                             ded_params={}, cal_tax_year=2023, years=[2027])
    assert out == {2027: {"400p": 1.0}}
    assert seen["spec"] is spec


def test_tail_gains_are_anchored_to_the_1m_target_before_tail_k_is_fit(no_synthesis, tail_units,
                                                                       ded_params, score):
    """The tail's gains hit the $1M+ share of the $400K+ Table 21 anchor at the base
    year, and the tail_k that results differs from the one fit on the unanchored tail
    (the gains' 7.25% alternative-tax cap moves the tail's tax)."""
    m = tail_units["is_synthetic_ultra_high"].to_numpy(dtype=bool)
    w = tail_units["weight"].to_numpy(float)
    anchored, k_tail = cg_anchor.anchor_tail_gains(tail_units)
    target = cg_anchor.anchored_nltcg_M()[-1] * cg_anchor.TOP_SHARE_1M * 1e6
    g = cg_anchor.model_gains(anchored["income"], anchored["synthetic_cg_share"])
    assert (g[m] * w[m]).sum() == pytest.approx(target, rel=1e-12)
    # income-consistent: only the gains moved; the PUMS rows are untouched
    g0 = cg_anchor.model_gains(tail_units["income"], tail_units["synthetic_cg_share"])
    np.testing.assert_allclose(anchored["income"] - tail_units["income"], (k_tail - 1) * g0 * m,
                               rtol=1e-12, atol=1e-6)
    np.testing.assert_allclose(anchored["primary_wagp"], tail_units["primary_wagp"])
    _, k_unanchored = calibrate_synthetic_tail_to_tax_target(score(tail_units), score=score)
    _, tail_k = build_units(tail_units, alpha=1.5, spec=AgingSpec(),
                            ded_params=ded_params, cal_tax_year=CAL_TAX_YEAR)
    assert tail_k != pytest.approx(k_unanchored, rel=1e-3)


def test_tail_k_scales_only_the_ordinary_income_so_the_gains_stay_at_the_anchor(
        no_synthesis, tail_units, score):
    m = tail_units["is_synthetic_ultra_high"].to_numpy(dtype=bool)
    w = tail_units["weight"].to_numpy(float)
    anchored, _ = cg_anchor.anchor_tail_gains(tail_units)
    target = cg_anchor.anchored_nltcg_M()[-1] * cg_anchor.TOP_SHARE_1M * 1e6
    out, k = calibrate_synthetic_tail_to_tax_target(score(anchored), score=score, scale_gains=False)
    g0 = cg_anchor.model_gains(anchored["income"], anchored["synthetic_cg_share"])
    g1 = cg_anchor.model_gains(out["income"], out["synthetic_cg_share"])
    assert k != pytest.approx(1.0, rel=1e-3)
    # the tail's gains equal the anchor after tail_k, row by row and in total
    np.testing.assert_allclose(g1[m], g0[m], rtol=1e-12)
    assert (g1[m] * w[m]).sum() == pytest.approx(target, rel=1e-9)
    # the ordinary income is what k scaled
    np.testing.assert_allclose((out["income"] - g1)[m], k * (anchored["income"] - g0)[m], rtol=1e-12)
    np.testing.assert_allclose(out.loc[m, "primary_wagp"], k * anchored.loc[m, "primary_wagp"], rtol=1e-12)
    np.testing.assert_allclose(out.loc[m, "investment_income"], anchored.loc[m, "investment_income"], rtol=1e-12)
    np.testing.assert_array_equal(out.loc[~m, "income"], anchored.loc[~m, "income"])
    # and the tail's tax lands on the DOTAX target as before
    tax = float((out.loc[m, "hi_tax_liability"] * out.loc[m, "weight"]).sum() / 1e6)
    assert tax == pytest.approx(DOTAX_1M_PLUS_TAX_TARGET_M, rel=1e-3)
