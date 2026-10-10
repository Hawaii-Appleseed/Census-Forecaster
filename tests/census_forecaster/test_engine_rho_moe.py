"""Tests for the October 2026 engine changes:

* `acs.correlations` — empirical member correlations from the fold cache
  and the literal-floored lookup chain.
* `projection.fit_damped_trend / fit_ar1_log_diff(moe_weighted=True)` —
  sampling-precision-weighted fits, with the unweighted path unchanged.
* Consumer gating in `project_ensemble_multi` (payload flags) and the
  calibration generator's payload keys / schema bump.
"""
from __future__ import annotations

import math
import random
from collections import defaultdict

import pytest
from census_forecaster.acs.calibration import FoldResidual, HoldOutFold, run_stratified_calibration
from census_forecaster.acs.correlations import (
    LITERAL_RHO,
    PAIR_ANCHOR_SOURCES,
    PAIR_COMPONENTS,
    PAIR_TARGET_ANCHOR,
    PAIR_TREND_ML,
    PAYLOAD_FLAG,
    PAYLOAD_KEY,
    RHO_MAX,
    build_rho_lookup,
    estimate_member_correlations,
    literal_rho_lookup,
    pearson,
    rho_lookup_from_calibration,
)
from census_forecaster.acs.ensemble import combine_forecasts, project_ensemble_multi
from census_forecaster.acs.projection import (
    MOE_WEIGHT_CAP,
    MOE_WEIGHT_FLOOR,
    _gain,
    _relative_precision_weights,
    fit_ar1_log_diff,
    fit_damped_trend,
    project_ar1_log_diff,
    project_damped_trend,
)
from census_forecaster.acs.strata import WILDCARD, record_from_dict
from census_forecaster.models import AcsObservation


def _obs(year, est, moe=1000.0, geoid="15003", indicator="B19013_001E"):
    return AcsObservation(
        estimate=est, moe=moe, year=year, vintage="1y", geoid=geoid, indicator=indicator,
    )


def _resid(method, geoid, anchor, h, actual, point, se=1000.0, indicator="B19013_001E"):
    return FoldResidual(
        indicator=indicator, method=method, geoid=geoid, anchor_year=anchor, horizon=h,
        pop_bucket="large", h_bucket="short" if h <= 2 else "long",
        actual=actual, point=point, se_total=se, se_sample=se / 2, se_forecast=se / 2,
        ci90_low=point - 1.645 * se, ci90_high=point + 1.645 * se,
    )


# -----------------------------------------------------------------------------
# correlations
# -----------------------------------------------------------------------------

class TestPearson:
    def test_perfect_positive(self):
        assert pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)

    def test_too_few_or_degenerate(self):
        assert pearson([1, 2], [1, 2]) is None
        assert pearson([1, 1, 1], [1, 2, 3]) is None


class TestEstimateMemberCorrelations:
    def _synthetic(self, rho_target: float, n: int = 60, seed: int = 1):
        """Two members whose log errors share a common shock at correlation rho_target."""
        rng = random.Random(seed)
        resid = []
        comps = []
        actual = 100_000.0
        for i in range(n):
            anchor = 2014 + i % 6
            h = 1 + i % 5
            shared = rng.gauss(0, 0.05)
            ea = rho_target * shared + math.sqrt(1 - rho_target ** 2) * rng.gauss(0, 0.05)
            eb = rho_target * shared + math.sqrt(1 - rho_target ** 2) * rng.gauss(0, 0.05)
            g = f"g{i}"
            comps.append(_resid("damped_log_trend", g, anchor, h, actual, actual * math.exp(ea)))
            comps.append(_resid("ar1_log_diff", g, anchor, h, actual, actual * math.exp(eb)))
            resid.append(_resid("trend_ensemble", g, anchor, h, actual, actual * math.exp(ea)))
            resid.append(_resid("multi_anchor", g, anchor, h, actual, actual * math.exp(eb)))
        return resid, comps

    def test_recovers_high_and_low_correlation(self):
        for target in (0.9, 0.1):
            resid, comps = self._synthetic(target, n=400)
            recs = estimate_member_correlations(resid, comps)
            by = {(r.method, r.h_bucket): r for r in recs if r.indicator == "B19013_001E"}
            comp = by[(PAIR_COMPONENTS, WILDCARD)]
            assert comp.n_folds == 400
            assert comp.value == pytest.approx(target, abs=0.08)
            # target_ensemble is trend alone here (no ML), so this pair
            # is the trend↔anchor correlation, same construction.
            anc = by[(PAIR_TARGET_ANCHOR, WILDCARD)]
            assert anc.value == pytest.approx(target, abs=0.08)

    def test_emits_h_bucket_records_and_clamps(self):
        resid, comps = self._synthetic(0.99, n=200)
        recs = estimate_member_correlations(resid, comps)
        hbs = {r.h_bucket for r in recs if r.method == PAIR_COMPONENTS}
        assert hbs == {"short", "long", WILDCARD}
        for r in recs:
            assert 0.0 <= r.value <= RHO_MAX
            assert "rho_raw" in r.extra

    def test_source_pairs_mean_pairwise(self):
        rng = random.Random(3)
        folds = []
        for i in range(50):
            e = rng.gauss(0, 0.05)
            for src, scale in (("cpi", 1.0), ("pce", 1.0)):
                folds.append(HoldOutFold(
                    indicator="B19013_001E", geoid=f"g{i}", anchor_year=2018,
                    target_year=2019, horizon=1, method=f"source:{src}",
                    actual=100.0, projected=100.0 * math.exp(scale * e + rng.gauss(0, 0.01)),
                    ci90_low=0, ci90_high=0,
                ))
        recs = estimate_member_correlations([], [], folds)
        src = [r for r in recs if r.method == PAIR_ANCHOR_SOURCES and r.h_bucket == WILDCARD]
        assert len(src) == 1
        assert src[0].value > 0.8
        assert "rho:cpi|pce" in src[0].extra

    def test_no_overlap_yields_no_record(self):
        resid = [_resid("trend_ensemble", "g", 2018, 1, 100.0, 101.0)]
        assert estimate_member_correlations(resid, []) == []


class TestRhoLookup:
    def _records(self):
        from census_forecaster.acs.strata import StrataRecord
        return [
            StrataRecord("B19013_001E", PAIR_COMPONENTS, WILDCARD, "short", 0.4, 25),
            StrataRecord("B19013_001E", PAIR_COMPONENTS, WILDCARD, "long", 0.3, 5),   # under n
            StrataRecord("B19013_001E", PAIR_COMPONENTS, WILDCARD, WILDCARD, 0.35, 30),
        ]

    def test_chain_exact_then_marginal_then_literal(self):
        rho = build_rho_lookup(self._records())
        assert rho("B19013_001E", PAIR_COMPONENTS, "short") == pytest.approx(0.4)
        assert rho("B19013_001E", PAIR_COMPONENTS, "long") == pytest.approx(0.35)  # n=5 skipped
        assert rho("B19013_001E", PAIR_COMPONENTS, None) == pytest.approx(0.35)
        assert rho("B25064_001E", PAIR_COMPONENTS, "short") == LITERAL_RHO[PAIR_COMPONENTS]
        assert rho("B19013_001E", PAIR_TREND_ML, "short") == LITERAL_RHO[PAIR_TREND_ML]

    def test_literal_lookup(self):
        for pair, lit in LITERAL_RHO.items():
            assert literal_rho_lookup("anything", pair, "short") == lit

    def test_payload_gate(self):
        from census_forecaster.acs.strata import record_to_dict
        recs = [record_to_dict(r) for r in self._records()]
        # Records present but flag off → literal.
        cal = {PAYLOAD_KEY: recs, PAYLOAD_FLAG: False}
        assert rho_lookup_from_calibration(cal)("B19013_001E", PAIR_COMPONENTS, "short") == 0.7
        cal[PAYLOAD_FLAG] = True
        assert rho_lookup_from_calibration(cal)("B19013_001E", PAIR_COMPONENTS, "short") == pytest.approx(0.4)
        assert rho_lookup_from_calibration(None)("x", PAIR_COMPONENTS, None) == 0.7


# -----------------------------------------------------------------------------
# MOE-weighted fits
# -----------------------------------------------------------------------------

class TestPrecisionWeights:
    def test_median_print_gets_unit_weight_and_clamps(self):
        # log SEs: 0.01 (very precise), 0.02, 0.02, 0.2 (very noisy)
        w = _relative_precision_weights([0.01, 0.02, 0.02, 0.2])
        assert w[1] == pytest.approx(1.0)
        assert w[0] == pytest.approx(MOE_WEIGHT_CAP)      # 4× capped (raw would be 4.0)
        assert w[3] == pytest.approx(MOE_WEIGHT_FLOOR)    # raw 0.01 → floored
        # Unknown MOE → 1.0; single known MOE → all ones.
        assert _relative_precision_weights([None, 0.02, 0.02]) [0] == 1.0
        assert _relative_precision_weights([None, 0.02]) == [1.0, 1.0]

    def test_gain_identity_at_unit_weight(self):
        for a in (0.2, 0.6, 0.9):
            assert _gain(a, 1.0) == pytest.approx(a)
            assert _gain(a, 4.0) > a
            assert _gain(a, 0.25) < a


class TestMoeWeightedFits:
    def _series(self, moes=None, n=8, seed=5):
        rng = random.Random(seed)
        out = []
        for i in range(n):
            est = 80_000 * 1.03 ** i * math.exp(rng.gauss(0, 0.01))
            moe = moes[i] if moes else 2000.0
            out.append(_obs(2012 + i, est, moe=moe))
        return out

    def test_unweighted_path_unchanged_by_flag_when_cv_uniform(self):
        # Equal log-SE (constant CV) → every relative weight is 1.0 → the
        # weighted recursions reduce exactly to the unweighted ones.
        obs = self._series()
        obs = [_obs(o.year, o.estimate, moe=0.03 * o.estimate) for o in obs]
        a = fit_damped_trend(obs)
        b = fit_damped_trend(obs, moe_weighted=True)
        assert (a.level, a.trend, a.residual_std) == pytest.approx((b.level, b.trend, b.residual_std))
        fa = fit_ar1_log_diff(obs)
        fb = fit_ar1_log_diff(obs, moe_weighted=True)
        assert (fa.rho, fa.intercept, fa.residual_std) == pytest.approx((fb.rho, fb.intercept, fb.residual_std))

    def test_default_flag_is_off_and_identical_to_old_signature(self):
        obs = self._series()
        assert fit_damped_trend(obs) == fit_damped_trend(obs, moe_weighted=False)
        assert project_damped_trend(obs, 2022) == project_damped_trend(obs, 2022, moe_weighted=False)
        assert project_ar1_log_diff(obs, 2022) == project_ar1_log_diff(obs, 2022, moe_weighted=False)

    def test_noisy_outlier_moves_weighted_fit_less(self):
        obs = self._series()
        # Inject a +25% spike on the last print with a huge MOE.
        spiked = list(obs[:-1]) + [_obs(obs[-1].year, obs[-1].estimate * 1.25, moe=40_000.0)]
        clean = fit_damped_trend(obs)
        unweighted = fit_damped_trend(spiked)
        weighted = fit_damped_trend(spiked, moe_weighted=True)
        assert abs(weighted.level - clean.level) < abs(unweighted.level - clean.level)
        assert abs(weighted.trend - clean.trend) < abs(unweighted.trend - clean.trend)
        # AR(1): the spike's diff row is down-weighted → rho closer to clean.
        c = fit_ar1_log_diff(obs)
        u = fit_ar1_log_diff(spiked)
        w = fit_ar1_log_diff(spiked, moe_weighted=True)
        assert abs(w.rho - c.rho) <= abs(u.rho - c.rho) + 1e-12

    def test_precise_print_cannot_dominate(self):
        obs = self._series()
        # One print with absurdly small MOE: weight is capped at MOE_WEIGHT_CAP,
        # so the gain stays strictly below 1 and the fit still smooths.
        precise = list(obs[:-1]) + [_obs(obs[-1].year, obs[-1].estimate * 1.10, moe=1.0)]
        w = fit_damped_trend(precise, moe_weighted=True)
        assert _gain(0.6, MOE_WEIGHT_CAP) == pytest.approx(0.6 * 4 / (0.6 * 4 + 0.4))
        # Level did not jump all the way to the spiked log value.
        assert w.level < math.log(precise[-1].estimate)

    def test_projection_runs_weighted(self):
        obs = self._series(moes=[2000, 2000, 9000, 2000, 2000, 2000, 500, 2000])
        fp = project_damped_trend(obs, 2022, moe_weighted=True)
        assert fp is not None and fp.se_total > 0
        fp2 = project_ar1_log_diff(obs, 2022, moe_weighted=True)
        assert fp2 is not None and fp2.se_total > 0


# -----------------------------------------------------------------------------
# Ensemble consumer
# -----------------------------------------------------------------------------

class TestEnsembleConsumer:
    def _hawaii(self):
        return [_obs(2012 + i, 70_000 * 1.03 ** i, moe=2500.0) for i in range(10)]

    def test_combine_forecasts_rho_param(self):
        obs = self._hawaii()
        comps = [project_damped_trend(obs, 2024), project_ar1_log_diff(obs, 2024)]
        hi = combine_forecasts(comps, 2024, rho=0.9)
        lo = combine_forecasts(comps, 2024, rho=0.1)
        default = combine_forecasts(comps, 2024)
        assert hi.point == pytest.approx(lo.point)  # ρ never moves the point
        assert hi.se_total > default.se_total > lo.se_total

    def test_moe_weighted_follows_payload_flag(self):
        obs = self._hawaii()
        obs[-2] = _obs(obs[-2].year, obs[-2].estimate * 1.15, moe=30_000.0)
        base = {"schema_version": 3, "strata_records": {}}
        off = project_ensemble_multi(obs, 2024, calibration=base, use_ml=False, use_kalman=False)
        on = project_ensemble_multi(
            obs, 2024, calibration={**base, "moe_weighted": True}, use_ml=False, use_kalman=False,
        )
        forced = project_ensemble_multi(obs, 2024, calibration=base, use_ml=False, use_kalman=False, moe_weighted=True)
        assert off.point != on.point
        assert on.point == pytest.approx(forced.point)

    def test_rho_follows_payload_flag(self):
        from census_forecaster.acs.strata import StrataRecord, record_to_dict
        obs = self._hawaii()
        recs = [record_to_dict(StrataRecord(
            "B19013_001E", PAIR_COMPONENTS, WILDCARD, WILDCARD, 0.05, 100,
        ))]
        base = {"schema_version": 3, "strata_records": {}, PAYLOAD_KEY: recs}
        off = project_ensemble_multi(obs, 2024, calibration=base, use_ml=False, use_kalman=False)
        on = project_ensemble_multi(
            obs, 2024, calibration={**base, PAYLOAD_FLAG: True}, use_ml=False, use_kalman=False,
        )
        assert off.point == pytest.approx(on.point)
        assert on.se_total < off.se_total  # ρ 0.05 vs 0.7 inside trend_ensemble


# -----------------------------------------------------------------------------
# Calibration generator
# -----------------------------------------------------------------------------

def _panel(seed=0, noise=0.04):
    rng = random.Random(seed)
    panel = defaultdict(list)
    for ind in ["B19013_001E", "B25064_001E"]:
        for g in ["15001", "15003", "15007", "15009"]:
            for i, y in enumerate(range(2010, 2025)):
                base = 80_000 * 1.025 ** i * math.exp(rng.gauss(0, noise))
                panel[(g, ind)].append(_obs(y, base, moe=2500 + 500 * (i % 3), geoid=g, indicator=ind))
    return dict(panel)


POPS = {"15001": 200_629, "15003": 1_009_506, "15007": 168_307, "15009": 73_135}


class TestCalibrationGenerator:
    def test_literal_run_emits_diagnostics_but_not_flags(self):
        payload = run_stratified_calibration(
            series_by_key=_panel(), anchor_years=[2016, 2017, 2018, 2019, 2020],
            horizons=(1, 2, 3), populations=POPS, enable_empirical_rho=False,
        )
        assert payload["schema_version"] in (3, 4)
        assert payload[PAYLOAD_FLAG] is False
        assert payload["moe_weighted"] is False
        recs = [record_from_dict(d) for d in payload[PAYLOAD_KEY]]
        pairs = {r.method for r in recs}
        assert PAIR_COMPONENTS in pairs and PAIR_TARGET_ANCHOR in pairs
        for r in recs:
            assert r.pop_bucket == WILDCARD
            assert 0.0 <= r.value <= RHO_MAX
        # No component methods leak into the κ/bias record sets.
        kappa_methods = {d["method"] for d in payload["strata_records"]["se_inflator"]}
        assert "damped_log_trend" not in kappa_methods and "ar1_log_diff" not in kappa_methods
        assert all(d["method"] not in ("damped_log_trend", "ar1_log_diff") for d in payload["fold_residuals"])

    def test_flags_bump_schema_and_change_folds(self):
        kw = dict(series_by_key=_panel(), anchor_years=[2016, 2017, 2018, 2019, 2020],
                  horizons=(1, 2, 3), populations=POPS)
        base = run_stratified_calibration(**kw, enable_empirical_rho=False)
        rho_on = run_stratified_calibration(**kw)  # empirical ρ is the default (2026-10-09)
        moe_on = run_stratified_calibration(**kw, moe_weighted=True, enable_empirical_rho=False)
        assert rho_on["schema_version"] == 6 and rho_on[PAYLOAD_FLAG] is True
        assert moe_on["schema_version"] == 6 and moe_on["moe_weighted"] is True
        # Empirical ρ changes trend_ensemble SEs but not points.
        pt = lambda p: [r for r in p["fold_residuals"] if r["method"] == "trend_ensemble"]  # noqa: E731
        a, b = pt(base), pt(rho_on)
        assert len(a) == len(b) > 0
        assert all(x["point"] == pytest.approx(y["point"]) for x, y in zip(a, b))
        assert any(abs(x["se_total"] - y["se_total"]) > 1e-9 for x, y in zip(a, b))
        # MOE weighting changes points (the MOEs vary by year in the panel).
        c = pt(moe_on)
        assert any(abs(x["point"] - y["point"]) > 1e-6 for x, y in zip(a, c))
        # Same correlation records either way (estimated from the same members).
        assert base[PAYLOAD_KEY] == rho_on[PAYLOAD_KEY]
