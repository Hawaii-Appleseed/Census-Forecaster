"""``act24_population.build_units``: the synthetic $1M+ tail is calibrated to
DOTAX's TY2023 $1M+ tax until re-scoring lands on it, then aged to the PUMS
income dollar year with the scenario's top-income premium (audit of September
29, 2026; see test_top_income_synthesis.py)."""
from __future__ import annotations

import numpy as np
import pytest
import tax_modeler.calibration.cg_anchor as cg_anchor
import tax_modeler.calibration.cg_imputation as cg_imputation
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


def test_top_premium_is_required(tail_units, ded_params):
    with pytest.raises(TypeError, match="top_premium"):
        build_units(tail_units, alpha=1.5, ded_params=ded_params, cal_tax_year=CAL_TAX_YEAR)


@pytest.fixture
def no_synthesis(monkeypatch):
    """build_units on a frame that already carries its tail: synthesis and the
    capital-gains imputation (which need the calibrated base) pass it through."""
    monkeypatch.setattr(top_income_synthesis, "synthesize_top_filers",
                        lambda df, pareto_alpha: df.copy())
    monkeypatch.setattr(cg_imputation, "impute_capital_gains_from_soi", lambda df: df)


@pytest.mark.parametrize("premium", [0.003, 0.010, 0.023])
def test_tail_calibrated_to_the_dotax_year_then_aged(no_synthesis, tail_units, ded_params, score,
                                             premium):
    units, tail_k = build_units(tail_units, alpha=1.5, top_premium=premium,
                                ded_params=ded_params, cal_tax_year=CAL_TAX_YEAR)

    calibrated, k = calibrate_synthetic_tail_to_tax_target(score(tail_units), score=score)
    assert tail_k == k
    m = tail_units["is_synthetic_ultra_high"].to_numpy(dtype=bool)
    tax = lambda df: float((df.loc[m, "hi_tax_liability"] * df.loc[m, "weight"]).sum() / 1e6)  # noqa: E731
    assert tax(calibrated) == pytest.approx(DOTAX_1M_PLUS_TAX_TARGET_M, rel=1e-3)

    g = synthetic_tail_aging_factor(premium)
    for col in INCOME_COLS:
        np.testing.assert_allclose(units.loc[m, col], tail_units.loc[m, col] * k * g,
                                   rtol=1e-12, err_msg=col)
        np.testing.assert_array_equal(units.loc[~m, col], tail_units.loc[~m, col], err_msg=col)
    # Re-scored on the calibration basis at the aged incomes.
    aged = score(age_synthetic_tail(calibrated, top_premium=premium))
    np.testing.assert_allclose(units["hi_tax_liability"], aged["hi_tax_liability"])
    assert tax(units) > DOTAX_1M_PLUS_TAX_TARGET_M * g * 0.99


def test_mid_gains_factors_builds_with_the_premium(monkeypatch, tail_units):
    seen = {}

    def fake_build_units(base, **kw):
        seen.update(kw)
        return base, 1.0

    monkeypatch.setattr(act24_population, "build_units", fake_build_units)
    monkeypatch.setattr(act24_population, "project_units", lambda u, **kw: u)
    monkeypatch.setattr(cg_anchor, "anchor_factors", lambda df, y: {"400p": 1.0})
    out = act24_population.mid_gains_factors(tail_units, alpha=1.5, top_premium=0.01,
                                             ded_params={}, cal_tax_year=2023, years=[2027])
    assert out == {2027: {"400p": 1.0}}
    assert seen["top_premium"] == 0.01
