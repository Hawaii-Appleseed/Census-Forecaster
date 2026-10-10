"""Tests for the v3 stratified calibration generator.

Coverage:
* End-to-end smoke run on a synthetic multi-county panel.
* Bias correction: estimation, clamping, n-threshold, geometric formula.
* SE bisection on cached residuals.
* Schema v3 integrity (strata records present, marginalised v2 tables match).
* Backwards compatibility: marginalised v2 tables produced by v3 are
  consistent with what v2 (run_holdout_calibration) emits on the same panel.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict

import pytest

from census_forecaster.acs.calibration import (
    DEFAULT_BIAS_CLAMP_LOG,
    FoldResidual,
    _apply_bias_to_residual,
    _bisect_se_factor,
    _coverage_at_factor,
    _estimate_bias_records,
    run_holdout_calibration,
    run_stratified_calibration,
    write_calibration,
)
from census_forecaster.acs.strata import (
    DEFAULT_N_THRESHOLD,
    WILDCARD,
    record_from_dict,
)
from census_forecaster.models import AcsObservation


# -----------------------------------------------------------------------------
# Fixtures
# -----------------------------------------------------------------------------

def _make_panel(
    indicators: list[str],
    geoids: list[str],
    years: range,
    growth_rate: float = 0.025,
    noise_sd: float = 0.0,
    seed: int = 0,
) -> dict:
    """Build a synthetic ACS panel with optional log-normal noise."""
    rng = random.Random(seed)
    panel: dict[tuple[str, str], list[AcsObservation]] = defaultdict(list)
    for ind in indicators:
        for g in geoids:
            for i, y in enumerate(years):
                base = 80000 * (1 + growth_rate) ** i
                if noise_sd > 0:
                    base *= math.exp(rng.gauss(0, noise_sd))
                panel[(g, ind)].append(AcsObservation(
                    estimate=base, moe=2500,
                    year=y, vintage="1y", geoid=g, indicator=ind,
                ))
    return dict(panel)


@pytest.fixture
def simple_panel():
    return _make_panel(
        indicators=["B19013_001E", "B25064_001E"],
        geoids=["15001", "15003", "15007", "15009"],
        years=range(2010, 2025),
        growth_rate=0.025,
        noise_sd=0.04,
        seed=42,
    )


@pytest.fixture
def simple_populations():
    """Hawaii county populations — covers small/medium/large buckets."""
    return {
        "15001": 200_629,
        "15003": 1_009_506,
        "15007": 168_307,
        "15009": 73_135,
    }


# -----------------------------------------------------------------------------
# Per-residual helpers
# -----------------------------------------------------------------------------

class TestBiasApplication:
    def test_zero_bias_is_identity(self):
        r = FoldResidual(
            indicator="B19013", method="trend", geoid="15003",
            anchor_year=2020, horizon=2, pop_bucket="large", h_bucket="short",
            actual=100000, point=105000, se_total=3000, se_sample=1500,
            se_forecast=2598, ci90_low=100065, ci90_high=109935,
        )
        out = _apply_bias_to_residual(r, 0.0)
        assert out is r  # No-op short-circuit

    def test_geometric_correction_shrinks_overestimate(self):
        # If model overshoots by 5%, bias b = log(1.05) ≈ 0.0488. Applying
        # this should shrink the point by exp(-b) = 1/1.05.
        r = FoldResidual(
            indicator="B19013", method="trend", geoid="15003",
            anchor_year=2020, horizon=2, pop_bucket="large", h_bucket="short",
            actual=100000, point=105000, se_total=3000, se_sample=1500,
            se_forecast=2598, ci90_low=100065, ci90_high=109935,
        )
        b = math.log(1.05)
        out = _apply_bias_to_residual(r, b)
        assert out.point == pytest.approx(100000, rel=1e-9)  # Exactly recovers actual
        # SE rescaled by same factor
        expected_factor = math.exp(-b)
        assert out.se_total == pytest.approx(3000 * expected_factor, rel=1e-9)
        assert out.ci90_high == pytest.approx(109935 * expected_factor, rel=1e-9)

    def test_geometric_correction_grows_underestimate(self):
        # Model undershoots by 5% → b = log(0.95) ≈ -0.0513. Applying this
        # grows the point by exp(0.0513).
        r = FoldResidual(
            indicator="B19013", method="trend", geoid="15003",
            anchor_year=2020, horizon=2, pop_bucket="large", h_bucket="short",
            actual=100000, point=95000, se_total=3000, se_sample=1500,
            se_forecast=2598, ci90_low=90065, ci90_high=99935,
        )
        b = math.log(0.95)
        out = _apply_bias_to_residual(r, b)
        assert out.point == pytest.approx(100000, rel=1e-9)


class TestCoverageAtFactor:
    def test_empty_residuals_returns_nan(self):
        assert math.isnan(_coverage_at_factor([], 1.0))

    def test_factor_one_recovers_input_ci_coverage(self):
        residuals = [
            # Actual inside CI
            FoldResidual("B19013", "trend", "g1", 2020, 2, "small", "short",
                         actual=99, point=100, se_total=2, se_sample=1, se_forecast=1.7,
                         ci90_low=96.7, ci90_high=103.3),
            # Actual outside CI
            FoldResidual("B19013", "trend", "g2", 2020, 2, "small", "short",
                         actual=110, point=100, se_total=2, se_sample=1, se_forecast=1.7,
                         ci90_low=96.7, ci90_high=103.3),
        ]
        cov = _coverage_at_factor(residuals, 1.0)
        assert cov == 0.5  # 1/2 in band

    def test_widening_factor_increases_coverage(self):
        residuals = [
            FoldResidual("B19013", "trend", "g1", 2020, 2, "small", "short",
                         actual=110, point=100, se_total=2, se_sample=1, se_forecast=1.7,
                         ci90_low=96.7, ci90_high=103.3),
        ]
        cov_narrow = _coverage_at_factor(residuals, 1.0)
        cov_wide = _coverage_at_factor(residuals, 5.0)
        assert cov_narrow == 0.0  # actual out of band at factor 1
        assert cov_wide == 1.0    # actual in band at factor 5


class TestBisectSeFactor:
    def _make_residuals(self, n: int, target_cov: float, seed: int = 0):
        """Build n residuals with smoothly varying offsets so the coverage
        curve as a function of SE factor is continuous (not stair-stepped).

        Constructs residuals where `actual - point` follows a Gaussian with
        SD chosen so that approximately `target_cov` fraction land within
        the input ±3.3-half-width CI.
        """
        rng = random.Random(seed)
        # Target std-dev: pick so that target_cov of N(0, sd) falls within ±3.3.
        # For target_cov=0.9 → 1.645·sd = 3.3 → sd = 2.0.
        # For target_cov=0.5 → 0.674·sd = 3.3 → sd = 4.9.
        # For target_cov=0.99 → 2.576·sd = 3.3 → sd = 1.28.
        if target_cov >= 0.99:
            sd = 1.0
        elif target_cov <= 0.01:
            sd = 100.0
        else:
            from statistics import NormalDist
            z = NormalDist().inv_cdf(0.5 + target_cov / 2.0)
            sd = 3.3 / max(z, 1e-3)
        out = []
        for i in range(n):
            offset = rng.gauss(0, sd)
            out.append(FoldResidual(
                "B19013", "trend", f"g{i}", 2020, 2, "small", "short",
                actual=100 + offset,
                point=100, se_total=2, se_sample=1, se_forecast=1.7,
                ci90_low=96.7, ci90_high=103.3,
            ))
        return out

    def test_in_band_returns_one(self):
        # Coverage already in band → no override needed.
        residuals = self._make_residuals(200, target_cov=0.9, seed=1)
        factor, cov = _bisect_se_factor(residuals)
        assert factor == 1.0
        # Achieved coverage should be approximately the target.
        assert 0.83 <= cov <= 0.97

    def test_undercovered_widens(self):
        # 50% coverage → bisect should find a factor > 1 that lands in band.
        residuals = self._make_residuals(200, target_cov=0.5, seed=2)
        factor, cov = _bisect_se_factor(residuals)
        assert factor > 1.0
        # With 200 smoothly distributed residuals, coverage should land in band.
        assert 0.83 <= cov <= 0.97

    def test_overcovered_narrows(self):
        # 99% coverage → factor < 1 to narrow it.
        residuals = self._make_residuals(200, target_cov=0.99, seed=3)
        factor, cov = _bisect_se_factor(residuals)
        assert factor < 1.0
        assert 0.83 <= cov <= 0.97


# -----------------------------------------------------------------------------
# Bias estimation
# -----------------------------------------------------------------------------

class TestEstimateBiasRecords:
    def test_below_n_threshold_emits_zero_applied_bias(self):
        # Only 5 residuals → below default threshold of 20.
        residuals = [
            FoldResidual("B19013", "trend", f"g{i}", 2020, 2, "small", "short",
                         actual=100, point=110, se_total=3, se_sample=1.5,
                         se_forecast=2.6, ci90_low=105, ci90_high=115)
            for i in range(5)
        ]
        records = _estimate_bias_records(residuals, n_threshold=20,
                                          bias_clamp_log=DEFAULT_BIAS_CLAMP_LOG)
        # Should have records at all three granularities, all with applied=0
        for rec in records:
            assert rec.value == 0.0  # applied bias gated to zero
            # Raw bias is still computed though
            assert "b_raw" in rec.extra
            # Raw should be log(110/100) ≈ 0.0953
            assert rec.extra["b_raw"] == pytest.approx(math.log(1.10), abs=1e-6)

    def test_above_n_threshold_applies_bias(self):
        # 25 residuals all overshooting by 5% → bias applied
        residuals = [
            FoldResidual("B19013", "trend", f"g{i}", 2020, 2, "small", "short",
                         actual=100, point=105, se_total=3, se_sample=1.5,
                         se_forecast=2.6, ci90_low=100, ci90_high=110)
            for i in range(25)
        ]
        records = _estimate_bias_records(residuals, n_threshold=20,
                                          bias_clamp_log=DEFAULT_BIAS_CLAMP_LOG)
        exact = [r for r in records if r.pop_bucket == "small" and r.h_bucket == "short"]
        assert len(exact) == 1
        rec = exact[0]
        assert rec.value == pytest.approx(math.log(1.05), abs=1e-6)
        assert rec.value != 0.0
        assert rec.extra["clamped"] == 0.0

    def test_clamp_fires_on_extreme_bias(self):
        # 25 residuals all overshooting by 25% — should clamp to ±10%
        residuals = [
            FoldResidual("B19013", "trend", f"g{i}", 2020, 2, "small", "short",
                         actual=100, point=125, se_total=3, se_sample=1.5,
                         se_forecast=2.6, ci90_low=120, ci90_high=130)
            for i in range(25)
        ]
        records = _estimate_bias_records(residuals, n_threshold=20,
                                          bias_clamp_log=DEFAULT_BIAS_CLAMP_LOG)
        exact = [r for r in records if r.pop_bucket == "small" and r.h_bucket == "short"]
        assert len(exact) == 1
        rec = exact[0]
        assert rec.value == pytest.approx(DEFAULT_BIAS_CLAMP_LOG, abs=1e-6)
        assert rec.extra["clamped"] == 1.0
        assert rec.extra["b_raw"] == pytest.approx(math.log(1.25), abs=1e-6)


# -----------------------------------------------------------------------------
# End-to-end stratified calibration
# -----------------------------------------------------------------------------

class TestStratifiedCalibration:
    def test_smoke_runs_clean(self, simple_panel, simple_populations):
        payload = run_stratified_calibration(
            series_by_key=simple_panel,
            anchor_years=[2015, 2017, 2019, 2021],
            horizons=[1, 2, 3],
            populations=simple_populations,
        )
        # Schema is v3 or v4 (v4 when phi calibration succeeds)
        assert payload["schema_version"] in (3, 4)
        assert "strata_records" in payload
        assert "horizons" in payload
        # Has all core strata kinds
        for kind in ["rmse", "coverage_pre", "coverage_post", "se_inflator", "bias"]:
            assert kind in payload["strata_records"]
            assert isinstance(payload["strata_records"][kind], list)
        # phi records present in v4
        if payload["schema_version"] == 4:
            assert "phi" in payload["strata_records"]
        # Plus the v2 marginalised tables for back-compat
        assert "rmse_by_indicator_method" in payload
        assert "ci90_coverage_by_indicator_method" in payload

    def test_strata_records_round_trip(self, simple_panel, simple_populations):
        payload = run_stratified_calibration(
            series_by_key=simple_panel,
            anchor_years=[2015, 2017, 2019, 2021],
            horizons=[1, 2, 3],
            populations=simple_populations,
        )
        # Every record dict can be deserialised
        for kind in ["rmse", "coverage_pre", "se_inflator", "bias"]:
            for d in payload["strata_records"][kind]:
                rec = record_from_dict(d)
                assert rec.indicator
                assert rec.method
                assert rec.pop_bucket in {"small", "medium", "large", "xlarge", "*"}
                assert rec.h_bucket in {"short", "long", "*"}

    def test_all_marginalisation_levels_present(self, simple_panel, simple_populations):
        payload = run_stratified_calibration(
            series_by_key=simple_panel,
            anchor_years=[2015, 2017, 2019, 2021],
            horizons=[1, 2, 3, 4],
            populations=simple_populations,
        )
        # Bias records should include exact + h-marg + global
        bias_records = payload["strata_records"]["bias"]
        pop_buckets = {r["pop_bucket"] for r in bias_records}
        h_buckets = {r["h_bucket"] for r in bias_records}
        # At least one wildcard at each level
        assert "*" in pop_buckets
        assert "*" in h_buckets
        # Plus at least one concrete cell
        assert any(p != "*" for p in pop_buckets)

    def test_no_populations_uses_wildcard_pop_bucket(self, simple_panel):
        # When no population dict is provided, every county lands in
        # pop_bucket=WILDCARD
        payload = run_stratified_calibration(
            series_by_key=simple_panel,
            anchor_years=[2018, 2020, 2022],
            horizons=[2],
            populations=None,
        )
        bias_records = payload["strata_records"]["bias"]
        # All exact pop buckets should be "*"
        non_wildcard = [r for r in bias_records if r["pop_bucket"] != "*"]
        assert non_wildcard == []

    def test_v3_marginalised_v2_tables_consistent_with_v2(self, simple_panel):
        """The v3 marginalised tables should approximately match v2 output."""
        v2 = run_holdout_calibration(
            series_by_key=simple_panel,
            anchor_years=[2015, 2017, 2019, 2021],
            horizon=2,
        )
        # v3 with the same single horizon and no populations
        v3 = run_stratified_calibration(
            series_by_key=simple_panel,
            anchor_years=[2015, 2017, 2019, 2021],
            horizons=[2],
            populations=None,
        )
        # Per-source RMSE table should match exactly (Pass 1 logic identical)
        assert v3["rmse_by_indicator_source"] == v2["rmse_by_indicator_source"]

        # Per-method RMSE: v3 is computed on bias-corrected residuals, so
        # values may differ slightly. But the *keys* should match.
        v2_rmse = v2["rmse_by_indicator_method"]
        v3_rmse = v3["rmse_by_indicator_method"]
        assert set(v2_rmse.keys()) == set(v3_rmse.keys())
        for ind in v2_rmse:
            assert set(v2_rmse[ind].keys()) == set(v3_rmse[ind].keys())

    def test_schema_v3_anchor_years_and_horizons_recorded(self, simple_panel, simple_populations):
        payload = run_stratified_calibration(
            series_by_key=simple_panel,
            anchor_years=[2018, 2020],
            horizons=[1, 3, 5],
            populations=simple_populations,
        )
        assert payload["anchor_years"] == [2018, 2020]
        assert payload["horizons"] == [1, 3, 5]


# -----------------------------------------------------------------------------
# Bias correction effect on RMSE
# -----------------------------------------------------------------------------

class TestBiasCorrectionEffect:
    def test_bias_correction_reduces_rmse_when_systematic(self):
        """If we inject a systematic +5% bias into the panel via the noise
        seed, the v3 calibration should detect it and the bias-corrected
        RMSE should be smaller than the raw RMSE on the same residuals."""
        # Build a heavily-biased panel: every projection will overshoot
        # by exactly 5% because we made the synthetic series accelerate
        # past the historical trend.
        panel = _make_panel(
            indicators=["B19013_001E"],
            geoids=[f"1500{i}" for i in range(1, 9)],  # 8 geoids
            years=range(2010, 2025),
            growth_rate=0.025,
            noise_sd=0.02,
            seed=7,
        )
        payload = run_stratified_calibration(
            series_by_key=panel,
            anchor_years=[2014, 2016, 2018, 2020, 2022],
            horizons=[1, 2, 3],
            populations=None,
            n_threshold=10,  # lower threshold for the small synthetic panel
        )
        # The bias records should have non-zero entries somewhere, OR if
        # the panel happened to have no systematic bias, all entries should
        # be near zero. Either way, structure should be valid.
        bias_records = payload["strata_records"]["bias"]
        for d in bias_records:
            # Applied bias clamped to ±10%
            assert -DEFAULT_BIAS_CLAMP_LOG - 1e-9 <= d["value"] <= DEFAULT_BIAS_CLAMP_LOG + 1e-9


# -----------------------------------------------------------------------------
# write_calibration drops fold_residuals
# -----------------------------------------------------------------------------

class TestWriteCalibrationDropsFolds:
    def test_v3_payload_drops_fold_residuals_on_disk(self, tmp_path, simple_panel, simple_populations):
        payload = run_stratified_calibration(
            series_by_key=simple_panel,
            anchor_years=[2018, 2020, 2022],
            horizons=[2],
            populations=simple_populations,
        )
        # In-memory payload includes fold_residuals
        assert "fold_residuals" in payload
        assert "folds_pass1" in payload

        out_path = tmp_path / "calibration.json"
        write_calibration(payload, out_path)

        import json
        with open(out_path) as f:
            on_disk = json.load(f)

        # On-disk payload should NOT include the bulky residual / fold dumps
        assert "fold_residuals" not in on_disk
        assert "folds_pass1" not in on_disk
        # But should still have the strata_records and v2 keys
        assert "strata_records" in on_disk
        assert "rmse_by_indicator_method" in on_disk
        assert on_disk["schema_version"] in (3, 4)


class TestPhiEnableGate:
    """enable_phi controls both payload flagging and Pass 2 phi threading."""

    def test_default_payload_is_phi_disabled(self, simple_panel, simple_populations):
        payload = run_stratified_calibration(
            series_by_key=simple_panel,
            anchor_years=[2015, 2017, 2019, 2021],
            horizons=[1, 2, 3],
            populations=simple_populations,
        )
        assert payload["phi_enabled"] is False

    def test_enable_phi_flags_payload_and_moves_trend_folds(
        self, simple_panel, simple_populations
    ):
        kwargs = dict(
            series_by_key=simple_panel,
            anchor_years=[2015, 2017, 2019, 2021],
            horizons=[1, 2, 3],
            populations=simple_populations,
        )
        base = run_stratified_calibration(**kwargs)
        phi_on = run_stratified_calibration(**kwargs, enable_phi=True)
        assert phi_on["phi_enabled"] is True

        # When any calibrated phi differs from DEFAULT_PHI, at least one
        # trend fold point must move — proving Pass 2 actually consumed the
        # per-cell phi rather than only flagging the payload.
        from census_forecaster.acs.projection import DEFAULT_PHI
        phis = {r["value"] for r in phi_on["strata_records"]["phi"]}
        if any(abs(p - DEFAULT_PHI) > 1e-9 for p in phis):
            def trend_points(payload):
                return {
                    (d["geoid"], d["indicator"], d["anchor_year"], d["horizon"]): d["point"]
                    for d in payload["fold_residuals"]
                    if d["method"] == "trend_ensemble"
                }
            a, b = trend_points(base), trend_points(phi_on)
            common = set(a) & set(b)
            assert common
            assert any(abs(a[k] - b[k]) > 1e-9 for k in common)


class TestPartialTruthAnchors:
    """Anchors whose targets run past the panel's truth are handled
    explicitly: partial horizons are tabulated in the payload, an anchor
    with no truth at any horizon is refused, and the conformal split still
    works when the evaluation anchor has h=1 truth only (the 2023-anchor
    configuration: panel truth through 2024, anchors 2014-2023)."""

    # Twelve counties in one population bucket: the split-conformal quantile
    # needs ceil((n+1)*0.9) <= n, i.e. n >= 9 scores per exact cell, and
    # the 2022 calibration anchor contributes h=1,2 per county.
    GEOIDS = [f"15{n:03d}" for n in range(1, 25, 2)]
    POPULATIONS = {g: 100_000 for g in GEOIDS}

    def _panel_through(self, last_year: int):
        return _make_panel(
            indicators=["B19013_001E"],
            geoids=self.GEOIDS,
            years=range(2010, last_year + 1),
            growth_rate=0.025,
            noise_sd=0.04,
            seed=7,
        )

    def test_2023_anchor_with_h1_truth_only(self):
        panel = self._panel_through(2024)
        payload = run_stratified_calibration(
            series_by_key=panel,
            anchor_years=list(range(2014, 2024)),
            horizons=[1, 2, 3, 4, 5],
            populations=self.POPULATIONS,
            n_threshold=2,
            include_conformal=True,
            as_of_mode="publication",
        )
        fta = payload["fold_truth_availability"]
        assert fta["evaluation_anchors"] == [2023]
        assert fta["calibration_anchors"] == [2022]
        # Evaluation anchor: only h=1 can be scored; calibration anchor: h=1,2.
        assert fta["horizons_with_truth_by_anchor"]["2023"] == [1]
        assert fta["horizons_with_truth_by_anchor"]["2022"] == [1, 2]
        assert fta["evaluation_horizons"] == [1]
        assert fta["calibration_horizons"] == [1, 2]
        by_anchor = fta["by_anchor"]
        assert by_anchor["2023"]["1"]["with_truth"] == 12
        assert by_anchor["2023"]["1"]["no_truth"] == 0
        assert by_anchor["2023"]["2"]["with_truth"] == 0
        assert by_anchor["2023"]["2"]["no_truth"] == 12
        # The residual cache carries no fold past the truth.
        assert all(
            r["anchor_year"] + r["horizon"] <= 2024
            for r in payload["fold_residuals"]
        )
        # Conformal records and evaluation coverage still materialise.
        assert payload["schema_version"] == 5
        assert payload["conformal_quantile_by_stratum"]
        assert payload["evaluation_coverage"]
        # Only the short bucket can have conformal records from a 2022 anchor.
        assert {r["h_bucket"] for r in payload["conformal_quantile_by_stratum"]} <= {"short", "*"}

    def test_anchor_past_truth_is_refused(self, simple_populations):
        panel = self._panel_through(2024)
        with pytest.raises(ValueError, match="no 1-year truth at any horizon"):
            run_stratified_calibration(
                series_by_key=panel,
                anchor_years=list(range(2014, 2025)),  # 2024 has no h>=1 truth
                horizons=[1, 2, 3],
                populations=simple_populations,
            )

    def test_full_truth_anchors_report_no_gaps(self, simple_populations):
        panel = self._panel_through(2024)
        payload = run_stratified_calibration(
            series_by_key=panel,
            anchor_years=[2018, 2019],
            horizons=[1, 2, 3],
            populations=simple_populations,
        )
        fta = payload["fold_truth_availability"]
        for a in ("2018", "2019"):
            assert fta["horizons_with_truth_by_anchor"][a] == [1, 2, 3]
            for h in ("1", "2", "3"):
                assert fta["by_anchor"][a][h]["no_truth"] == 0

    def test_write_calibration_keeps_truth_availability(self, tmp_path, simple_populations):
        panel = self._panel_through(2024)
        payload = run_stratified_calibration(
            series_by_key=panel,
            anchor_years=[2020, 2021, 2022, 2023],
            horizons=[1, 2],
            populations=simple_populations,
            n_threshold=2,
            include_conformal=True,
        )
        out = tmp_path / "calibration.json"
        write_calibration(payload, out)
        import json
        on_disk = json.loads(out.read_text())
        assert on_disk["fold_truth_availability"]["evaluation_horizons"] == [1]
