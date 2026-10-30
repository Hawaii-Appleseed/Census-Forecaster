"""Tests for the back-test harness."""
import math

import pytest
from census_forecaster.backtest.acs import (
    DEFAULT_METHODS,
    project_carry_forward,
    project_linear_log,
    run_backtest,
    truncate_to_anchor,
)
from census_forecaster.models import AcsObservation


def _obs(year, est, moe=1000.0, vintage="1y"):
    return AcsObservation(
        estimate=est, moe=moe, year=year, vintage=vintage,
        geoid="15003", indicator="B19013_001E",
    )


class TestTruncateToAnchor:
    def test_keeps_observations_at_or_before(self):
        obs = [_obs(2018, 100), _obs(2019, 105), _obs(2020, 110), _obs(2021, 115)]
        kept = truncate_to_anchor(obs, anchor_year=2019)
        years = [o.year for o in kept]
        assert years == [2018, 2019]

    def test_5y_uses_midpoint_for_truncation(self):
        # 2024 5y vintage → effective_year 2022; should be kept when
        # anchor=2022 even though `year` field is 2024.
        obs = [_obs(2024, 100, vintage="5y")]
        assert truncate_to_anchor(obs, anchor_year=2022) == obs
        assert truncate_to_anchor(obs, anchor_year=2021) == []

    def test_empty_input(self):
        assert truncate_to_anchor([], anchor_year=2024) == []


class TestProjectCarryForward:
    def test_baseline_returns_latest(self):
        obs = [_obs(2018, 100), _obs(2024, 105)]
        fp = project_carry_forward(obs, target_year=2026)
        assert fp.point == 105
        assert fp.method == "carry_forward"
        # CI should come from MOE only (no model uncertainty).
        assert fp.se_forecast == 0
        assert fp.se_sample > 0

    def test_returns_none_for_empty(self):
        assert project_carry_forward([], target_year=2026) is None


class TestProjectLinearLog:
    def test_constant_series_zero_slope(self):
        obs = [_obs(y, 100) for y in range(2018, 2025)]
        fp = project_linear_log(obs, target_year=2026)
        assert fp.point == pytest.approx(100, rel=1e-6)

    def test_geometric_recovers_slope(self):
        obs = [_obs(y, 100 * (1.04 ** i)) for i, y in enumerate(range(2018, 2025))]
        fp = project_linear_log(obs, target_year=2026)
        # 100 · 1.04^9 ≈ 142.3 (anchor year 2018, target 2026 = 8 years out from
        # midpoint of fitted line, which sits closer to ~2021)
        # Just check the projection is in a sensible range above the latest.
        latest = obs[-1].estimate
        assert latest < fp.point < latest * 1.20

    def test_cap_extreme_growth(self):
        obs = [_obs(y, 100 * (1.40 ** i)) for i, y in enumerate(range(2018, 2025))]
        fp = project_linear_log(obs, target_year=2027)
        rate = (fp.point / obs[-1].estimate) ** (1.0 / fp.horizon) - 1.0
        assert rate <= 0.10 + 1e-6

    def test_returns_none_for_too_few_points(self):
        assert project_linear_log([_obs(2024, 100)], target_year=2026) is None

    def test_handles_zero_variance_in_x(self):
        # All observations at the same year → singular regression.
        # (Constructing this requires bypassing GeographySeries dup-check;
        # we use raw obs list.)
        obs = [_obs(2024, 100), _obs(2024, 110)]
        # Same year is unusual but the linear-log projector should not
        # divide by zero — return None.
        result = project_linear_log(obs, target_year=2026)
        assert result is None


class TestRunBacktest:
    def _build_panel(self):
        # Simple geometric series, two geographies × one indicator.
        panel = {}
        for geoid in ("15003", "15001"):
            obs = [
                AcsObservation(
                    estimate=100 * (1.025 ** i), moe=500,
                    year=y, vintage="1y",
                    geoid=geoid, indicator="B19013_001E",
                )
                for i, y in enumerate(range(2010, 2025))
            ]
            panel[(geoid, "B19013_001E")] = obs
        return panel

    def test_runs_all_default_methods(self):
        panel = self._build_panel()
        summaries = run_backtest(panel, anchors=[2018, 2020, 2022], horizon=2)
        for m in DEFAULT_METHODS:
            assert m in summaries

    def test_carry_forward_has_largest_mape_on_growing_series(self):
        # On a steadily growing series, the trend models should beat
        # carry-forward — pinning the basic invariant.
        panel = self._build_panel()
        summaries = run_backtest(panel, anchors=[2018, 2020, 2022], horizon=2)
        cf = summaries["carry_forward"].mean_abs_pct_error
        ens = summaries["ensemble"].mean_abs_pct_error
        assert ens < cf

    def test_skips_anchors_with_no_target(self):
        # If anchor + horizon falls outside the series, the fold drops
        # silently (no exception).
        panel = self._build_panel()
        # 2030 + 2 = 2032, beyond the test series → no rows scored.
        summaries = run_backtest(panel, anchors=[2030], horizon=2)
        for m in summaries.values():
            assert m.n == 0

    def test_runs_with_no_panel(self):
        summaries = run_backtest({}, anchors=[2020, 2022], horizon=2)
        for m in summaries.values():
            assert m.n == 0
            assert math.isnan(m.mean_abs_pct_error)


class TestMultiHorizonBacktest:
    """Phase 5: backtest harness supports a list of horizons."""

    def _build_panel(self):
        panel = {}
        for geoid in ("15003", "15001"):
            obs = [
                AcsObservation(
                    estimate=100 * (1.025 ** i), moe=500,
                    year=y, vintage="1y",
                    geoid=geoid, indicator="B19013_001E",
                )
                for i, y in enumerate(range(2010, 2025))
            ]
            panel[(geoid, "B19013_001E")] = obs
        return panel

    def test_horizons_kwarg_pools_folds(self):
        panel = self._build_panel()
        # With horizons=[1, 2, 3], each anchor produces up to 3× the folds
        s_single = run_backtest(panel, anchors=[2015, 2017, 2019], horizon=2)
        s_multi = run_backtest(panel, anchors=[2015, 2017, 2019], horizons=[1, 2, 3])
        # ensemble should run on both; multi pools more rows
        assert s_multi["ensemble"].n > s_single["ensemble"].n

    def test_summarise_by_horizon_splits_correctly(self):
        from census_forecaster.backtest.acs import summarise_by_horizon
        panel = self._build_panel()
        s = run_backtest(panel, anchors=[2015, 2017, 2019], horizons=[1, 2, 3])
        rolls = summarise_by_horizon(s["ensemble"].rows, "ensemble")
        # All three horizons should be present
        assert set(rolls.keys()) == {1, 2, 3}
        # Per-horizon row counts should sum to total
        total = sum(r.n for r in rolls.values())
        assert total == s["ensemble"].n

    def test_summarise_by_strata_classifies_buckets(self):
        from census_forecaster.backtest.acs import summarise_by_strata
        panel = self._build_panel()
        # Toy populations: 15003 -> xlarge (1.0M), 15001 -> medium (168K)
        pops = {"15003": 1_009_506, "15001": 168_307}
        s = run_backtest(panel, anchors=[2015, 2017, 2019], horizons=[1, 2, 3])
        rolls = summarise_by_strata(s["ensemble"].rows, populations=pops)
        # Should have entries for both pop buckets × both horizon buckets
        bucket_keys = set(rolls.keys())
        assert ("xlarge", "short") in bucket_keys
        assert ("medium", "short") in bucket_keys
        # h=1,2 → "short"; h=3 → "long"
        assert any(b == "long" for _, b in bucket_keys)

    def test_horizon_back_compat_default(self):
        """Old callers passing only `horizon=N` still work unchanged."""
        panel = self._build_panel()
        s_old = run_backtest(panel, anchors=[2018, 2020], horizon=2)
        # Should work, produce one h=2 fold per (anchor, geoid)
        assert s_old["ensemble"].n > 0
        # All rows should have horizon=2
        assert all(r.horizon == 2 for r in s_old["ensemble"].rows)

    def test_unknown_geoid_falls_into_unknown_bucket(self):
        from census_forecaster.backtest.acs import summarise_by_strata
        panel = self._build_panel()
        s = run_backtest(panel, anchors=[2018, 2020], horizons=[2])
        # Empty population dict → all rows go to "unknown" bucket
        rolls = summarise_by_strata(s["ensemble"].rows, populations={})
        for (pop_b, h_b), summary in rolls.items():
            assert pop_b == "unknown"
