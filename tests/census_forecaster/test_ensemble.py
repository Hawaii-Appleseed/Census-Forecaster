"""Tests for the ensemble combiner + macro-anchor blending."""
import math

import pytest

from census_forecaster.acs.ensemble import (
    combine_forecasts,
    macro_anchor_projection,
    project_ensemble,
)
from census_forecaster.models import AcsObservation, ForecastPoint
from census_forecaster.acs.projection import ANNUAL_RATE_CAP


def _obs(year, est, moe=1000.0, vintage="1y"):
    return AcsObservation(
        estimate=est, moe=moe, year=year, vintage=vintage,
        geoid="15003", indicator="B19013_001E",
    )


def _fp(point, se, method="m1", target_year=2026, horizon=2):
    return ForecastPoint(
        point=point, se_total=se, se_sample=se / 2, se_forecast=se / 2,
        ci90_low=point - 1.645 * se, ci90_high=point + 1.645 * se,
        method=method, target_year=target_year, geoid="15003",
        indicator="B19013_001E", horizon=horizon,
    )


class TestCombineForecasts:
    def test_inverse_variance_weighting(self):
        # Two forecasts: (100, σ=10) and (110, σ=5). Inverse-variance
        # weights: 1/100, 1/25 → normalised 1/5, 4/5. Combined point =
        # 100·1/5 + 110·4/5 = 20 + 88 = 108.
        a = _fp(100, 10)
        b = _fp(110, 5, method="m2")
        c = combine_forecasts([a, b], target_year=2026)
        assert c.point == pytest.approx(108.0, rel=1e-6)

    def test_combined_se_with_correlation(self):
        # Two forecasts with equal SE=5 → inverse-variance weights w=0.5
        # each. With cross-correlation ρ=0.7 the closed-form variance is
        #   Var = 2·w²·σ²·(1 + ρ)
        #       = 2·0.25·25·1.7 = 21.25
        #   SE  = sqrt(21.25) ≈ 4.6098
        a = _fp(100, 5)
        b = _fp(110, 5, method="m2")
        c = combine_forecasts([a, b], target_year=2026)
        expected = math.sqrt(2 * 0.25 * 25 * (1 + 0.7))
        assert c.se_total == pytest.approx(expected, rel=1e-6)

    def test_combined_ci_includes_point(self):
        a = _fp(100, 10)
        b = _fp(120, 5, method="m2")
        c = combine_forecasts([a, b], target_year=2026)
        assert c.ci90_low <= c.point <= c.ci90_high

    def test_empty_returns_none(self):
        assert combine_forecasts([], target_year=2026) is None

    def test_mismatched_target_raises(self):
        a = _fp(100, 10, target_year=2025)
        b = _fp(110, 5, method="m2", target_year=2026)
        with pytest.raises(ValueError):
            combine_forecasts([a, b], target_year=2026)

    def test_mismatched_geoid_raises(self):
        a = _fp(100, 10)
        b = ForecastPoint(
            point=110, se_total=5, se_sample=2, se_forecast=2,
            ci90_low=100, ci90_high=120, method="m2",
            target_year=2026, geoid="15001", indicator="B19013_001E", horizon=2,
        )
        with pytest.raises(ValueError):
            combine_forecasts([a, b], target_year=2026)

    def test_zero_variance_falls_back_to_equal_weights(self):
        # Degenerate case: if every forecast has zero SE, inverse-variance
        # is undefined. Should not divide by zero.
        a = _fp(100, 0)
        b = _fp(110, 0, method="m2")
        c = combine_forecasts([a, b], target_year=2026)
        # Equal-weight average: (100 + 110) / 2 = 105.
        assert c.point == pytest.approx(105.0, rel=1e-9)


class TestMacroAnchor:
    def test_basic_compounding(self):
        latest = _obs(2024, 100, moe=0)
        fp = macro_anchor_projection(latest, target_year=2026, annual_growth_rate=0.05)
        # 100 · 1.05² = 110.25
        assert fp.point == pytest.approx(110.25, rel=1e-9)

    def test_negative_rate_compounds(self):
        latest = _obs(2024, 100, moe=0)
        fp = macro_anchor_projection(latest, target_year=2026, annual_growth_rate=-0.03)
        assert fp.point == pytest.approx(100 * (0.97 ** 2), rel=1e-9)

    def test_extreme_rate_capped(self):
        latest = _obs(2024, 100, moe=0)
        fp = macro_anchor_projection(latest, target_year=2026, annual_growth_rate=0.50)
        # Cap kicks in at +10%/yr.
        assert fp.point == pytest.approx(100 * (1 + ANNUAL_RATE_CAP) ** 2, rel=1e-6)
        assert "capped" in fp.notes

    def test_negative_extreme_rate_capped(self):
        latest = _obs(2024, 100, moe=0)
        fp = macro_anchor_projection(latest, target_year=2026, annual_growth_rate=-0.40)
        assert fp.point == pytest.approx(100 * (1 - ANNUAL_RATE_CAP) ** 2, rel=1e-6)
        assert "capped" in fp.notes

    def test_horizon_zero_passthrough(self):
        latest = _obs(2024, 100, moe=10)
        fp = macro_anchor_projection(latest, target_year=2024, annual_growth_rate=0.05)
        assert fp.point == 100
        assert fp.method == "macro_anchor"


class TestProjectEnsemble:
    def test_full_pipeline_growing_series(self):
        obs = [_obs(y, 100 * (1.025 ** i)) for i, y in enumerate(range(2010, 2025))]
        fp = project_ensemble(obs, target_year=2026)
        assert fp is not None
        assert fp.method == "ensemble"
        assert fp.point > obs[-1].estimate

    def test_returns_none_with_empty_input(self):
        assert project_ensemble([], target_year=2026) is None

    def test_returns_none_with_one_obs_no_macro(self):
        # AR(1) needs ≥4, damped trend needs ≥2 — one observation alone
        # gives nothing to ensemble, no macro to fall back to → None.
        assert project_ensemble([_obs(2024, 100)], target_year=2026) is None

    def test_macro_anchor_fallback_with_sparse_data(self):
        # One observation + macro anchor → macro_anchor result.
        fp = project_ensemble(
            [_obs(2024, 100, moe=0)],
            target_year=2026,
            macro_annual_rate=0.04,
            macro_weight=0.30,
        )
        assert fp is not None
        # 1.04² = 1.0816
        assert fp.point == pytest.approx(108.16, rel=1e-6)

    def test_macro_blend_weight_respected(self):
        obs = [_obs(y, 100 * (1.02 ** i)) for i, y in enumerate(range(2010, 2025))]
        # No macro: pure trend
        fp_trend = project_ensemble(obs, target_year=2026)
        # With macro at 0% growth, weight 0.5 → projection pulled toward latest.
        fp_blend = project_ensemble(
            obs, target_year=2026, macro_annual_rate=0.0, macro_weight=0.5,
        )
        # Blend should sit between latest (100·1.02^14 ≈ 132.0) and trend forecast.
        latest = obs[-1].estimate
        assert latest < fp_blend.point < fp_trend.point

    def test_ensemble_ci_includes_point(self):
        obs = [_obs(y, 100 * (1.02 ** i)) for i, y in enumerate(range(2010, 2025))]
        fp = project_ensemble(obs, target_year=2026)
        assert fp.ci90_low <= fp.point <= fp.ci90_high

    def test_ensemble_includes_method_weights_in_notes(self):
        obs = [_obs(y, 100 * (1.02 ** i)) for i, y in enumerate(range(2010, 2025))]
        fp = project_ensemble(obs, target_year=2026)
        assert "damped_log_trend" in fp.notes
        assert "ar1_log_diff" in fp.notes


# -----------------------------------------------------------------------------
# Tests for project_ensemble_multi with use_ml=True
# -----------------------------------------------------------------------------

class TestEnsembleMultiWithMl:
    """Verify that the optional ml_trend third member integrates cleanly."""

    def _toy_panel(self, n_counties=60, n_years=12):
        series = {}
        pops = {}
        for c in range(n_counties):
            geoid = f"15{c:03d}"
            pops[geoid] = 50_000 + c * 20_000
            for ind_idx, ind in enumerate(("B19013_001E", "B25058_001E")):
                obs_list = []
                base = 50_000.0 + ind_idx * 5_000 + c * 200
                for y in range(2010, 2010 + n_years):
                    val = base * (1.02 ** (y - 2010)) * (1.0 + 0.001 * c)
                    obs_list.append(_obs(y, val))
                # Re-stamp geoid/indicator on obs (the helper used (year, est))
                obs_list = [
                    type(o)(estimate=o.estimate, moe=o.moe, year=o.year,
                           vintage=o.vintage, geoid=geoid, indicator=ind)
                    for o in obs_list
                ]
                series[(geoid, ind)] = obs_list
        return series, pops

    def test_default_is_use_ml_true(self):
        """use_ml defaults to True since the 2026-08-21 ship-gate pass
        (see backtests/results/ml_default_ablation_2026-08-21.md)."""
        import inspect
        from census_forecaster.acs.ensemble import project_ensemble_multi
        sig = inspect.signature(project_ensemble_multi)
        assert sig.parameters["use_ml"].default is True

    def test_out_of_panel_geoid_degrades_to_classical(self):
        """A geoid absent from the ML panel gets no feature row, so the
        default path must produce the classical ensemble unchanged."""
        from census_forecaster.acs.ensemble import project_ensemble_multi
        series, pops = self._toy_panel()
        obs = [o for o in series[("15030", "B19013_001E")] if o.year <= 2020]

        fp_default = project_ensemble_multi(obs, target_year=2022, populations=pops)
        fp_explicit = project_ensemble_multi(obs, target_year=2022, populations=pops, use_ml=False)
        assert fp_default is not None
        assert fp_explicit is not None
        assert "ml_trend" not in fp_default.notes
        assert fp_default.point == pytest.approx(fp_explicit.point)
        assert fp_default.se_total == pytest.approx(fp_explicit.se_total)

    def test_default_path_engages_ml_for_panel_county(self):
        """For a county in the bundled panel (Honolulu), the default path
        must include the ml_trend member via the lazy panel load."""
        import census_forecaster.acs.ensemble as ens
        bundled = ens._load_bundled_ml_panel()
        if not bundled:
            pytest.skip("bundled calibration panel not present")
        obs = [
            o for o in bundled["series"][("15003", "B19013_001E")]
            if o.year <= 2023 and o.vintage == "1y"
        ]
        fp = ens.project_ensemble_multi(obs, target_year=2026)
        assert fp is not None
        assert "ml_trend" in fp.notes

    def test_use_ml_true_inserts_third_member(self):
        from census_forecaster.acs.ensemble import project_ensemble_multi
        from census_forecaster.acs.ml_features import build_panel_index
        series, pops = self._toy_panel()
        obs = [o for o in series[("15030", "B19013_001E")] if o.year <= 2020]
        panel = build_panel_index(series)
        cache = {}

        fp = project_ensemble_multi(
            obs, target_year=2022, populations=pops,
            use_ml=True, ml_series_by_key=series, ml_populations=pops,
            ml_model_cache=cache, ml_panel=panel,
        )
        assert fp is not None
        # ml_trend should appear in the ensemble notes
        assert "ml_trend" in fp.notes
        # Cache populated by exactly one model fit at this cutoff
        assert ("B19013_001E", 2020) in cache
        # CI brackets the point
        assert fp.ci90_low <= fp.point <= fp.ci90_high

    def test_use_ml_true_loads_bundled_panel_when_none_supplied(self, monkeypatch):
        """use_ml=True with no panel kwargs lazily loads the bundled panel."""
        import census_forecaster.acs.ensemble as ens
        series, pops = self._toy_panel()
        obs = [o for o in series[("15030", "B19013_001E")] if o.year <= 2020]
        # Substitute the toy panel for the bundled one so the test stays
        # fast and hermetic; the lookup path is identical.
        from census_forecaster.acs.ml_features import build_panel_index
        monkeypatch.setattr(ens, "_ML_BUNDLED", None)
        monkeypatch.setattr(
            ens, "_load_bundled_ml_panel",
            lambda: {"series": series, "populations": pops,
                     "panel": build_panel_index(series)},
        )
        fp = ens.project_ensemble_multi(
            obs, target_year=2022, populations=pops,
            use_ml=True, ml_series_by_key=None, ml_populations=None,
        )
        assert fp is not None
        assert "ml_trend" in fp.notes

    def test_use_ml_true_falls_back_when_panel_missing(self, monkeypatch):
        """When no panel is supplied AND the bundled panel is unavailable,
        fall back gracefully to trend+anchor."""
        import census_forecaster.acs.ensemble as ens
        series, pops = self._toy_panel()
        obs = [o for o in series[("15030", "B19013_001E")] if o.year <= 2020]
        monkeypatch.setattr(ens, "_load_bundled_ml_panel", lambda: None)
        fp = ens.project_ensemble_multi(
            obs, target_year=2022, populations=pops,
            use_ml=True, ml_series_by_key=None, ml_populations=None,
        )
        assert fp is not None  # falls back to trend+anchor only
        assert "ml_trend" not in fp.notes


class TestPublicApiAlias:
    def test_project_acs_ensemble_is_the_calibrated_path(self):
        """The advertised public API must be the production (calibrated)
        ensemble, not the legacy uncorrected trend path."""
        import census_forecaster
        from census_forecaster.acs.ensemble import project_ensemble_multi

        assert census_forecaster.project_acs_ensemble is project_ensemble_multi
        assert census_forecaster.project_ensemble_multi is project_ensemble_multi


class TestKalmanPromotion:
    """Per-indicator Kalman promotion (2026-08-21).

    The wholesale Kalman verdict is HOLD; only indicators that passed
    both per-indicator ship gates are promoted (see
    KALMAN_PROMOTED_INDICATORS and phase_d_kalman_ablation.md).
    """

    def _series(self, indicator, geoid="15003", n=12):
        from census_forecaster.models import AcsObservation
        return [
            AcsObservation(
                estimate=100.0 * (1.02 ** i), moe=5.0,
                year=2010 + i, vintage="1y", geoid=geoid, indicator=indicator,
            )
            for i in range(n)
        ]

    def test_allowlist_contents_are_the_gated_indicators(self):
        from census_forecaster.acs.ensemble import KALMAN_PROMOTED_INDICATORS
        assert KALMAN_PROMOTED_INDICATORS == {
            "B25071_001E", "pct_service_occupations",
        }

    def test_promoted_indicator_uses_kalman_by_default(self):
        from census_forecaster.acs.ensemble import project_ensemble_multi
        from census_forecaster.kalman import METHOD_NAME
        obs = self._series("B25071_001E")
        fp = project_ensemble_multi(obs, target_year=2023, use_ml=False)
        assert fp is not None
        assert fp.method == METHOD_NAME

    def test_non_promoted_indicator_ignores_kalman_by_default(self):
        from census_forecaster.acs.ensemble import project_ensemble_multi
        from census_forecaster.kalman import METHOD_NAME
        obs = self._series("B19013_001E")
        fp = project_ensemble_multi(obs, target_year=2023, use_ml=False)
        assert fp is not None
        assert fp.method != METHOD_NAME

    def test_explicit_false_disables_kalman_for_promoted_indicator(self):
        from census_forecaster.acs.ensemble import project_ensemble_multi
        from census_forecaster.kalman import METHOD_NAME
        obs = self._series("B25071_001E")
        fp = project_ensemble_multi(obs, target_year=2023, use_ml=False, use_kalman=False)
        assert fp is not None
        assert fp.method != METHOD_NAME

    def test_explicit_true_forces_kalman_for_any_indicator(self):
        from census_forecaster.acs.ensemble import project_ensemble_multi
        from census_forecaster.kalman import METHOD_NAME
        obs = self._series("B19013_001E")
        fp = project_ensemble_multi(obs, target_year=2023, use_ml=False, use_kalman=True)
        assert fp is not None
        assert fp.method == METHOD_NAME


class TestAnchorSeFoldParity:
    """Production multi_anchor SE must equal the calibration-fold SE.

    The multi_anchor κ / bias / conformal records are fit on folds built by
    ``calibration._project_anchor_only``; ``project_ensemble_multi`` applies
    them to its own anchor forecast. Both must convert the per-source RMSE
    to a per-year SE with the same divisor (ANCHOR_CALIBRATION_HORIZON) at
    every h. Production used to divide by the target h instead, so its
    pre-κ SE was ~2× the fold SE at h=1 and ~0.4× at h=5.
    """

    ANCHOR_YEAR = 2018
    RMSE = 0.40  # large enough that the calibration SE binds over the in-sample floor

    def _series(self, indicator):
        return [
            AcsObservation(
                estimate=80000.0 * (1.03 ** i), moe=1500.0,
                year=2010 + i, vintage="1y", geoid="15003", indicator=indicator,
            )
            for i in range(self.ANCHOR_YEAR - 2010 + 1)
        ]

    def _per_source(self, indicator):
        from census_forecaster.acs.sources import available_sources
        names = [s.name for s in available_sources(indicator) if s.anchor_type == "rate"]
        assert names, f"no rate-type anchor source for {indicator}"
        return {indicator: {n: self.RMSE for n in names}}

    def test_default_divisor_is_the_shared_constant(self):
        import inspect
        from census_forecaster.acs.anchors import (
            ANCHOR_CALIBRATION_HORIZON, combined_anchor_rate,
        )
        default = inspect.signature(combined_anchor_rate).parameters[
            "calibration_horizon"
        ].default
        assert default == ANCHOR_CALIBRATION_HORIZON == 2

    @pytest.mark.parametrize("indicator", ["B19013_001E", "B25064_001E"])
    @pytest.mark.parametrize("h", [1, 2, 3, 4, 5])
    def test_production_anchor_se_matches_fold_se(self, monkeypatch, indicator, h):
        import census_forecaster.acs.anchors as anchors_mod
        from census_forecaster.acs.anchors import (
            ANCHOR_CALIBRATION_HORIZON, combined_anchor_rate,
        )
        from census_forecaster.acs.calibration import _project_anchor_only
        from census_forecaster.acs.ensemble import project_ensemble_multi

        train = self._series(indicator)
        per_source = self._per_source(indicator)
        target = self.ANCHOR_YEAR + h

        # Non-vacuous: the calibration RMSE (not the in-sample SD) sets
        # every component SE, so the divisor actually matters.
        rate = combined_anchor_rate(
            indicator=indicator, end_year=self.ANCHOR_YEAR,
            calibration=per_source, geoid="15003",
        )
        assert rate is not None
        for _name, _r, se, _w in rate.components:
            assert se == pytest.approx(self.RMSE / ANCHOR_CALIBRATION_HORIZON)

        fold = _project_anchor_only(
            train, target, self.ANCHOR_YEAR, indicator, per_source_rmse=per_source,
        )
        assert fold is not None

        # Capture the raw (pre-bias, pre-κ) anchor forecast production builds.
        captured: list[ForecastPoint] = []
        real = anchors_mod.anchor_as_forecast

        def _spy(*args, **kwargs):
            fp = real(*args, **kwargs)
            captured.append(fp)
            return fp

        monkeypatch.setattr(anchors_mod, "anchor_as_forecast", _spy)
        out = project_ensemble_multi(
            train, target,
            calibration={"rmse_by_indicator_source": per_source},
            populations={}, use_ml=False, use_kalman=False,
        )
        assert out is not None
        assert len(captured) == 1
        prod = captured[0]
        assert prod.horizon == fold.horizon == h
        assert prod.point == pytest.approx(fold.point, rel=1e-12)
        assert prod.se_forecast == pytest.approx(fold.se_forecast, rel=1e-12)
        assert prod.se_total == pytest.approx(fold.se_total, rel=1e-12)
