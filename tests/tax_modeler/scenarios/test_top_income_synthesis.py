"""The synthetic $1M+ tail's tax calibration and its aging to the PUMS dollar
year (audit of September 29, 2026).

(A) ``rescale_synthetic_tail_to_tax_target`` applied ``k = target / actual``
    once and claimed that landed within 0.5%. Tax is not proportional to
    income at the top (deduction, lower brackets, §235-51(f) alternative tax),
    so the re-scored Act 24 tails overshot $663M by 2-4%.
    ``calibrate_synthetic_tail_to_tax_target`` iterates to the target.
(B) The $663M target is DOTAX TY2022, but the projection grows every unit
    from the PUMS income dollar year (2024), so the tail never received its
    2022-2024 growth. ``age_synthetic_tail`` supplies it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.liability.federal import add_federal_income_columns
from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR
from tax_modeler.scenarios import top_income_synthesis as tis
from tax_modeler.scenarios.top_income_synthesis import (
    DOTAX_1M_PLUS_TARGET_TAX_YEAR,
    age_synthetic_tail,
    calibrate_synthetic_tail_to_tax_target,
    rescale_synthetic_tail_to_tax_target,
    synthetic_tail_aging_factor,
)

INCOME_COLS = ["income", "agi", "synthetic_total_income", "earned_income",
               "investment_income", "primary_wagp", "primary_intp"]


def _tail(df: pd.DataFrame) -> np.ndarray:
    return df["is_synthetic_ultra_high"].to_numpy(dtype=bool)


def _tail_tax_m(df: pd.DataFrame) -> float:
    m = _tail(df)
    return float((df.loc[m, "hi_tax_liability"] * df.loc[m, "weight"]).sum() / 1e6)


# ---------------------------------------------------------------------------
# (A) the tax calibration
# ---------------------------------------------------------------------------

class TestCalibrateToTaxTarget:

    @pytest.mark.parametrize("ratio", [1.45, 0.8], ids=["scale-up", "scale-down"])
    def test_one_step_misses_the_target(self, tail_units, score, ratio):
        scored = score(tail_units)
        target = ratio * _tail_tax_m(scored)
        stepped, _ = rescale_synthetic_tail_to_tax_target(scored, target_tax_m=target)
        assert abs(_tail_tax_m(score(stepped)) / target - 1) > 0.005

    @pytest.mark.parametrize("ratio", [1.45, 0.8], ids=["scale-up", "scale-down"])
    def test_converges_to_the_target(self, tail_units, score, ratio):
        scored = score(tail_units)
        target = ratio * _tail_tax_m(scored)
        out, k = calibrate_synthetic_tail_to_tax_target(scored, score=score, target_tax_m=target)
        assert _tail_tax_m(out) == pytest.approx(target, rel=1e-3)
        _, k1 = rescale_synthetic_tail_to_tax_target(scored, target_tax_m=target)
        assert (k < k1) if ratio > 1 else (k > k1)   # the single step overshoots
        # The tail scaled by k and scored whole; the PUMS units untouched.
        m = _tail(out)
        for col in INCOME_COLS:
            np.testing.assert_allclose(out.loc[m, col], scored.loc[m, col] * k, rtol=1e-12)
            np.testing.assert_array_equal(out.loc[~m, col], scored.loc[~m, col])
        np.testing.assert_array_equal(out.loc[~m, "hi_tax_liability"],
                                      scored.loc[~m, "hi_tax_liability"])
        from_scratch = score(tis._scale_synthetic_incomes(scored, k))
        np.testing.assert_allclose(out["hi_tax_liability"], from_scratch["hi_tax_liability"])

    def test_federal_income_follows_the_tail(self, tail_units, score):
        scored = score(tail_units)
        out, _ = calibrate_synthetic_tail_to_tax_target(
            scored, score=score, target_tax_m=1.3 * _tail_tax_m(scored))
        rebuilt = add_federal_income_columns(out)
        for col in ("federal_agi", "federal_earned_income"):
            np.testing.assert_allclose(out[col], rebuilt[col])

    def test_raises_when_it_does_not_converge(self, tail_units, score):
        scored = score(tail_units)
        with pytest.raises(RuntimeError, match="not within"):
            calibrate_synthetic_tail_to_tax_target(
                scored, score=score, target_tax_m=1.45 * _tail_tax_m(scored), max_iter=1)

    def test_needs_scored_units(self, tail_units, score):
        with pytest.raises(ValueError, match="score the units first"):
            calibrate_synthetic_tail_to_tax_target(
                tail_units.assign(hi_tax_liability=0.0), score=score)


# ---------------------------------------------------------------------------
# (B) aging from TY2022 to the PUMS dollar year
# ---------------------------------------------------------------------------

class TestAgeToPumsDollarYear:

    def test_the_targets_are_ty2022(self):
        from tax_modeler.calibration.forward_targets import (
            _DOTAX_FILER_TARGETS_2022,
            _DOTAX_TAX_TARGETS_2022,
        )
        top = (1_000_000, np.inf)
        assert DOTAX_1M_PLUS_TARGET_TAX_YEAR == 2022
        assert _DOTAX_FILER_TARGETS_2022[top] == tis.DOTAX_1M_PLUS_FILER_TARGET
        assert _DOTAX_TAX_TARGETS_2022[top] == tis.DOTAX_1M_PLUS_TAX_TARGET_M

    def test_observed_levels_are_the_projectors_anchors(self):
        # The bundled panel observation project_revenue_per_filer anchors
        # Honolulu's growth on, in the PUMS dollar year and in TY2022.
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer
        for year in (PUMS_INCOME_DOLLAR_YEAR, DOTAX_1M_PLUS_TARGET_TAX_YEAR):
            proj = project_revenue_per_filer(target_year=2027, geoid="15003", anchor_year=year)
            assert proj.anchor_income == tis._observed_honolulu_b19013(year)

    def test_factor_is_observed_b19013_growth_times_the_premium(self):
        from census_forecaster.acs.projection import effective_year
        from tax_modeler.projection.income_forecast import _load_b19013_series
        level = {int(effective_year(o)): o.estimate
                 for o in _load_b19013_series("15003") if o.vintage == "1y"}
        years = PUMS_INCOME_DOLLAR_YEAR - DOTAX_1M_PLUS_TARGET_TAX_YEAR
        b19013 = level[PUMS_INCOME_DOLLAR_YEAR] / level[DOTAX_1M_PLUS_TARGET_TAX_YEAR]
        assert b19013 > 1
        assert synthetic_tail_aging_factor(0.0) == pytest.approx(b19013, rel=1e-12)
        for p in (0.003, 0.010, 0.023):
            assert synthetic_tail_aging_factor(p) == pytest.approx(b19013 * (1 + p) ** years,
                                                                   rel=1e-12)

    def test_no_growth_from_the_targets_own_year(self):
        assert synthetic_tail_aging_factor(0.01, year=DOTAX_1M_PLUS_TARGET_TAX_YEAR) == 1.0

    def test_scales_only_the_tail_and_clears_its_tax(self, tail_units, score):
        scored = score(tail_units)
        aged = age_synthetic_tail(scored, top_premium=0.01)
        g = synthetic_tail_aging_factor(0.01)
        m = _tail(aged)
        for col in INCOME_COLS:
            np.testing.assert_allclose(aged.loc[m, col], scored.loc[m, col] * g, rtol=1e-12)
            np.testing.assert_array_equal(aged.loc[~m, col], scored.loc[~m, col])
        assert (aged.loc[m, "hi_tax_liability"] == 0).all()
        np.testing.assert_array_equal(aged.loc[~m, "hi_tax_liability"],
                                      scored.loc[~m, "hi_tax_liability"])
        rebuilt = add_federal_income_columns(aged)
        np.testing.assert_allclose(aged["federal_agi"], rebuilt["federal_agi"])
        assert _tail_tax_m(score(aged)) > g * 0.99 * _tail_tax_m(scored)
