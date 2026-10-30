"""Unit tests for compute_cpi_ratio() and helpers in pipelines/grocery/src/price_adjuster.py.

These cover the projection edge cases introduced in the P0 refactor — particularly
the 'exact' vs 'projected' vs 'interpolated' vs 'unavailable' method disambiguation.
"""
from datetime import date

import pytest

# The grocery pipeline lives in its own package tree at pipelines/grocery/.
# Inserting its root lets us import from src.price_adjuster directly.
# Package import — no path injection needed.
from census_forecaster.bls.projection import (  # noqa: E402
    _RESIDUAL_LOG_STD_PRIOR,
    PROJ_DAMPING,
    PROJ_MONTHLY_CAP,
    ProjectionResult,
    compute_cpi_ratio,
    damped_compound_factor,
    forecast_se_log,
    project_forward,
    project_forward_full,
    residual_log_std,
    smoothed_monthly_rate,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_point(year: int, month: int, value: float) -> dict:
    """Build a BLS-shaped data point (year is int, matching cpi_fetcher output)."""
    return {"year": year, "period": f"M{month:02d}", "value": value}


def _cpi_data(series_id: str, points: list[dict]) -> dict:
    return {series_id: points}


SERIES = "CUURS49ASAF11"


# ---------------------------------------------------------------------------
# compute_cpi_ratio
# ---------------------------------------------------------------------------

class TestComputeCpiRatio:
    def test_exact_match(self):
        """When target == a bimonthly observation, method should be 'exact'."""
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 303.0),
            _make_point(2024, 6, 306.0),
        ]
        cpi_data = _cpi_data(SERIES, pts)
        result = compute_cpi_ratio(cpi_data, SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 6, 1))
        assert result["method"] == "exact"
        assert result["ratio"] == pytest.approx(306.0 / 300.0)
        assert not result["is_projected"]

    def test_interpolated_between_points(self):
        """Target between two observations → method 'interpolated'."""
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 304.0),
        ]
        cpi_data = _cpi_data(SERIES, pts)
        result = compute_cpi_ratio(cpi_data, SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 3, 1))
        assert result["method"] == "interpolated"
        # March is halfway between Feb and Apr → ratio ≈ 302 / 300
        assert result["ratio"] == pytest.approx(302.0 / 300.0, rel=1e-3)
        assert not result["is_projected"]

    def test_projected_beyond_latest(self):
        """Target past last observation → method 'projected', is_projected True."""
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 306.0),
        ]
        cpi_data = _cpi_data(SERIES, pts)
        result = compute_cpi_ratio(cpi_data, SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 6, 1))
        assert result["method"] == "projected"
        assert result["is_projected"] is True
        assert result["ratio"] > 1.0  # projection should exceed baseline

    def test_unavailable_empty_series(self):
        result = compute_cpi_ratio({}, SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 4, 1))
        assert result["method"] == "unavailable"
        assert result["ratio"] == pytest.approx(1.0)
        assert not result["is_projected"]

    def test_same_baseline_and_target_ratio_is_one(self):
        """Identical baseline and target dates → ratio must be 1.0."""
        pts = [_make_point(2024, 4, 305.0)]
        cpi_data = _cpi_data(SERIES, pts)
        result = compute_cpi_ratio(cpi_data, SERIES,
                                   baseline_date=date(2024, 4, 1),
                                   target_date=date(2024, 4, 1))
        assert result["ratio"] == pytest.approx(1.0)

    def test_latest_observed_iso_format(self):
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 305.0),
        ]
        cpi_data = _cpi_data(SERIES, pts)
        result = compute_cpi_ratio(cpi_data, SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 4, 1))
        assert result["latest_observed"] == "2024-04"

    def test_missing_series_key_is_unavailable(self):
        result = compute_cpi_ratio({"other_series": []}, SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 4, 1))
        assert result["method"] == "unavailable"


# ---------------------------------------------------------------------------
# _project_forward
# ---------------------------------------------------------------------------

class TestProjectForward:
    def test_flat_series_stays_flat(self):
        """Two identical points → growth rate 0 → projected value == latest."""
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 300.0),
        ]
        result = project_forward(pts, date(2024, 6, 1))
        assert result == pytest.approx(300.0)

    def test_growing_series_projects_higher(self):
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 306.0),   # +1% per month over 2 months
        ]
        result = project_forward(pts, date(2024, 6, 1))
        assert result > 306.0

    def test_monthly_rate_cap_applied(self):
        """A very noisy bimonthly spike should be capped at ±0.0189/month."""
        pts = [
            _make_point(2024, 2, 100.0),
            _make_point(2024, 4, 200.0),   # +100% in 2 months — far above cap
        ]
        # Cap: 0.0189/month × 2 months beyond = ~3.8% max increase from 200
        result = project_forward(pts, date(2024, 6, 1))
        # Uncapped would be 200 * (1 + 0.414)^2 ≈ 283; capped should be ~207
        assert result < 210.0, f"cap not applied: {result}"

    def test_single_point_returns_that_value(self):
        """Only one observation → can't compute growth rate → return latest."""
        pts = [_make_point(2024, 4, 300.0)]
        result = project_forward(pts, date(2024, 6, 1))
        assert result == pytest.approx(300.0)

    def test_empty_series_raises(self):
        with pytest.raises(ValueError, match="cannot project forward from empty series"):
            project_forward([], date(2024, 6, 1))

    def test_smoothing_dilutes_one_off_spike(self):
        """A spike on the latest print should not dominate when prior trend was flat.

        Three flat points + one spike → smoothed rate is well below the
        single-pair rate from just the last two points. This is the whole
        point of recency-weighted smoothing: avoid being whipsawed by a
        single noisy bimonthly print.
        """
        flat_then_spike = [
            _make_point(2024, 1, 300.0),
            _make_point(2024, 3, 300.0),
            _make_point(2024, 5, 300.0),
            _make_point(2024, 7, 312.0),    # +4% jump on the last print
        ]
        smoothed = smoothed_monthly_rate(flat_then_spike)
        # Naive 2-point rate: (312/300)^(1/2) - 1 ≈ 0.0198/mo
        # Smoothed rate (weights 0.125, 0.25, 0.5, normalised over last 3 pairs):
        # 0% + 0% + 1.98% blended → well under 0.0198, but positive.
        assert 0.0 < smoothed < 0.012

    def test_damped_factor_decays_with_horizon(self):
        """Damped compounding should grow slower than undamped at long horizons."""
        rate = 0.01  # 1% per month
        damped_6mo = damped_compound_factor(rate, 6)
        undamped_6mo = (1 + rate) ** 6
        assert damped_6mo < undamped_6mo
        # Damping factor PROJ_DAMPING < 1 → projected slope decays each month.
        assert 0 < PROJ_DAMPING < 1.0

    def test_damped_factor_flat_when_rate_zero(self):
        """Zero growth → damped factor is exactly 1.0 at any horizon."""
        assert damped_compound_factor(0.0, 12) == pytest.approx(1.0)

    def test_smoothing_collapses_to_pair_with_two_points(self):
        """With exactly 2 points, smoothed rate == pairwise rate (back-compat)."""
        pts = [_make_point(2024, 2, 300.0), _make_point(2024, 4, 306.0)]
        smoothed = smoothed_monthly_rate(pts)
        pairwise = (306.0 / 300.0) ** 0.5 - 1.0
        assert smoothed == pytest.approx(pairwise, rel=1e-9)


# ---------------------------------------------------------------------------
# YoY trend path (seasonality-free, cadence-invariant smoother)
# ---------------------------------------------------------------------------

def _seasonal_series(months, g=0.0025, amp=0.01, start_year=2023,
                     start_month=1, step=1, base=100.0):
    """NSA-style fixture: exponential trend g/mo + period-12 seasonality."""
    import math as _m
    pts = []
    for k in range(months):
        m_abs = (start_year * 12 + start_month - 1) + k * step
        y, m = divmod(m_abs, 12)
        val = base * _m.exp(g * k * step + amp * _m.sin(2 * _m.pi * (m_abs % 12) / 12))
        pts.append(_make_point(y, m + 1, val))
    return pts


class TestYoySmoothing:
    """The YoY path activates on long series and ignores seasonality."""

    G = 0.0025  # ~3.05%/yr underlying trend

    def test_yoy_recovers_trend_through_seasonality(self):
        """On a trending series with period-12 seasonality, the smoothed
        rate equals the trend exactly — same-month differencing cancels
        the seasonal term by construction."""
        pts = _seasonal_series(26, g=self.G)
        import math as _m
        assert smoothed_monthly_rate(pts) == pytest.approx(
            _m.expm1(self.G), rel=1e-9)

    def test_yoy_cadence_invariance(self):
        """Monthly and bimonthly samplings of the same path agree —
        the recency decay is per calendar month, not per print."""
        monthly = _seasonal_series(26, g=self.G, step=1)
        bimonthly = _seasonal_series(14, g=self.G, step=2)
        r_m = smoothed_monthly_rate(monthly)
        r_b = smoothed_monthly_rate(bimonthly)
        assert r_m == pytest.approx(r_b, rel=1e-9)

    def test_yoy_stable_under_final_print_spike(self):
        """A +2% spike on the newest print must barely move the trend —
        the failure mode of the pairwise smoother (which put ~50% weight
        on the newest pair) that this path exists to fix."""
        pts = _seasonal_series(26, g=self.G, amp=0.0)
        spiked = pts[:-1] + [_make_point(
            pts[-1]["year"], int(pts[-1]["period"][1:]),
            pts[-1]["value"] * 1.02)]
        swing = abs(smoothed_monthly_rate(spiked) - smoothed_monthly_rate(pts))
        assert swing < 5e-4  # <0.05pp/mo; pairwise swung ~1pp/mo here

    def test_yoy_survives_missing_print(self):
        """An isolated hole (cf. the 2025-10 shutdown gap) only drops the
        YoY pairs touching it; the trend estimate is unaffected."""
        pts = _seasonal_series(14, g=self.G, step=2)
        holed = pts[:6] + pts[7:]
        import math as _m
        assert smoothed_monthly_rate(holed) == pytest.approx(
            _m.expm1(self.G), rel=1e-9)

    def test_yoy_max_pairs_restricts_lookback(self):
        """On an accelerating series, restricting to the newest YoY obs
        yields a higher rate than the full window."""
        slow = _seasonal_series(14, g=0.001, amp=0.0)
        last = slow[-1]
        y0, m0 = last["year"], int(last["period"][1:])
        fast = list(slow)
        v = last["value"]
        for k in range(1, 13):
            m_abs = (y0 * 12 + m0 - 1) + k
            yy, mm = divmod(m_abs, 12)
            v *= 1.005
            fast.append(_make_point(yy, mm + 1, v))
        r_narrow = smoothed_monthly_rate(fast, max_pairs=2)
        r_full = smoothed_monthly_rate(fast)
        assert r_narrow > r_full

    def test_below_min_yoy_obs_falls_back_to_pairwise(self):
        """With <4 YoY observations the legacy pairwise smoother runs,
        bit-for-bit (its short-series behavior is a public contract)."""
        # 15 monthly points → 3 YoY obs → fallback.
        pts = _seasonal_series(15, g=self.G, amp=0.0)
        expected_pairs = []
        for i in range(1, len(pts)):
            prev, curr = pts[i - 1], pts[i]
            mb = ((curr["year"] - prev["year"]) * 12
                  + int(curr["period"][1:]) - int(prev["period"][1:]))
            rate = (curr["value"] / prev["value"]) ** (1.0 / mb) - 1.0
            expected_pairs.append((rate, 0.5 ** (len(pts) - 1 - i)))
        expected = (sum(r * w for r, w in expected_pairs)
                    / sum(w for _, w in expected_pairs))
        assert smoothed_monthly_rate(pts) == pytest.approx(expected, rel=1e-12)


# ---------------------------------------------------------------------------
# Projection uncertainty surface
# ---------------------------------------------------------------------------

class TestProjectionUncertainty:
    """compute_cpi_ratio surfaces forecast SE / CI / cap-firing on projected rows.

    For exact / interpolated / unavailable rows, all projection-only fields
    are None — backwards-compatible default for callers that only know
    about ratio / method / is_projected.
    """

    def test_projection_only_fields_none_when_exact(self):
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 304.0),
            _make_point(2024, 6, 308.0),
        ]
        result = compute_cpi_ratio(_cpi_data(SERIES, pts),
                                   SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 6, 1))
        assert result["method"] == "exact"
        for key in ("cap_fired", "monthly_rate", "implied_annual_rate",
                    "forecast_se", "ratio_ci90_low", "ratio_ci90_high",
                    "horizon_months"):
            assert result[key] is None, f"{key} should be None on exact match"

    def test_projection_only_fields_none_when_interpolated(self):
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 304.0),
        ]
        result = compute_cpi_ratio(_cpi_data(SERIES, pts),
                                   SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 3, 1))
        assert result["method"] == "interpolated"
        assert result["forecast_se"] is None
        assert result["cap_fired"] is None

    def test_projected_row_populates_uncertainty_fields(self):
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 303.0),
            _make_point(2024, 6, 306.0),  # ~+1% / 2mo, well below cap
        ]
        result = compute_cpi_ratio(_cpi_data(SERIES, pts),
                                   SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 9, 1))   # 3 months past
        assert result["method"] == "projected"
        assert result["cap_fired"] is False
        assert isinstance(result["monthly_rate"], float)
        assert isinstance(result["implied_annual_rate"], float)
        assert result["forecast_se"] is not None and result["forecast_se"] >= 0.0
        assert result["ratio_ci90_low"] <= result["ratio"] <= result["ratio_ci90_high"]
        assert result["horizon_months"] == 3

    def test_forecast_se_grows_with_horizon(self):
        """SE is monotonically non-decreasing in horizon under damped trend."""
        pts = [
            _make_point(2024, 1, 300.0),
            _make_point(2024, 3, 302.0),
            _make_point(2024, 5, 304.0),
            _make_point(2024, 7, 306.0),
        ]
        cpi_data = _cpi_data(SERIES, pts)
        h1 = compute_cpi_ratio(cpi_data, SERIES,
                               baseline_date=date(2024, 1, 1),
                               target_date=date(2024, 8, 1))
        h3 = compute_cpi_ratio(cpi_data, SERIES,
                               baseline_date=date(2024, 1, 1),
                               target_date=date(2024, 10, 1))
        assert h1["forecast_se"] is not None and h3["forecast_se"] is not None
        assert h1["forecast_se"] < h3["forecast_se"], (
            f"SE should grow with horizon: h=1 SE={h1['forecast_se']}, "
            f"h=3 SE={h3['forecast_se']}"
        )

    def test_cap_fired_true_on_extreme_spike(self):
        """A 100→200 spike (≈ +41%/mo over 2mo) must fire the ±0.0189 cap."""
        pts = [
            _make_point(2024, 2, 100.0),
            _make_point(2024, 4, 200.0),
        ]
        result = compute_cpi_ratio(_cpi_data(SERIES, pts),
                                   SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 6, 1))
        assert result["method"] == "projected"
        assert result["cap_fired"] is True
        # Capped rate equals exactly +PROJ_MONTHLY_CAP
        assert result["monthly_rate"] == pytest.approx(PROJ_MONTHLY_CAP)

    def test_cap_fired_false_on_normal_growth(self):
        """1% / 2mo growth is far below the cap — must report cap_fired=False."""
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 303.0),
        ]
        result = compute_cpi_ratio(_cpi_data(SERIES, pts),
                                   SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 6, 1))
        assert result["method"] == "projected"
        assert result["cap_fired"] is False

    def test_ci_widens_above_cap_when_capped(self):
        """When cap fires, upper CI must reach at least the uncapped projection.

        Without this, the CI would lie below the value the model would
        have produced sans cap — under-stating projection risk on the
        side that the cap was hiding.
        """
        pts = [
            _make_point(2024, 2, 100.0),
            _make_point(2024, 4, 200.0),
        ]
        result = compute_cpi_ratio(_cpi_data(SERIES, pts),
                                   SERIES,
                                   baseline_date=date(2024, 2, 1),
                                   target_date=date(2024, 6, 1))
        # Uncapped point would have been ~200 × (1.41×0.92×1.41) — far
        # above the capped point of ~207. Upper CI must reflect that.
        assert result["ratio_ci90_high"] > result["ratio"]
        # Sanity: CI upper must be at least the uncapped point estimate / baseline
        # (which is large for this extreme spike).
        proj = project_forward_full(pts, date(2024, 6, 1))
        baseline_cpi = 100.0
        assert result["ratio_ci90_high"] >= proj.point_uncapped / baseline_cpi - 1e-9

    def testresidual_log_std_falls_back_to_prior_for_n2(self):
        """With only one pair, residual std is undefined → use the documented prior."""
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 303.0),
        ]
        smoothed = smoothed_monthly_rate(pts)
        std = residual_log_std(pts, smoothed)
        assert std == pytest.approx(_RESIDUAL_LOG_STD_PRIOR)


class TestProjectForwardFull:
    """ProjectionResult dataclass is the diagnostic-rich projection output."""

    def test_returns_projection_result(self):
        pts = [
            _make_point(2024, 2, 300.0),
            _make_point(2024, 4, 303.0),
        ]
        proj = project_forward_full(pts, date(2024, 6, 1))
        assert isinstance(proj, ProjectionResult)
        assert proj.value > 0
        assert proj.horizon_months == 2
        assert proj.cap_fired is False

    def test_max_pairs_window_caps_smoother_lookback(self):
        """max_pairs=2 ignores older points; smoothed rate = blend of last 2 pairs."""
        pts = [
            _make_point(2023, 1, 300.0),
            _make_point(2023, 3, 295.0),  # early flat-down
            _make_point(2023, 5, 295.0),
            _make_point(2024, 1, 300.0),
            _make_point(2024, 3, 304.0),  # +0.66%/mo on last pair
        ]
        proj_all = project_forward_full(pts, date(2024, 6, 1), max_pairs=None)
        proj_2 = project_forward_full(pts, date(2024, 6, 1), max_pairs=2)
        # Restricting to last 2 pairs should give a higher rate (latest
        # pair is +0.66%/mo, earlier pairs were flat or negative).
        assert proj_2.monthly_rate > proj_all.monthly_rate

    def test_phi_one_disables_damping(self):
        """phi=1 reduces damped compounding to flat compound rate."""
        pts = [
            _make_point(2024, 2, 100.0),
            _make_point(2024, 4, 101.0),
        ]
        proj_undamped = project_forward_full(pts, date(2024, 10, 1), phi=1.0)
        proj_damped   = project_forward_full(pts, date(2024, 10, 1), phi=0.92)
        # Both have a positive trend → undamped projection should be
        # strictly higher than damped at h=6.
        assert proj_undamped.value > proj_damped.value

    def test_inflator_scales_se_linearly(self):
        pts = [
            _make_point(2024, 1, 300.0),
            _make_point(2024, 3, 302.0),
            _make_point(2024, 5, 304.0),
        ]
        p1 = project_forward_full(pts, date(2024, 8, 1), inflator=1.0)
        p2 = project_forward_full(pts, date(2024, 8, 1), inflator=2.0)
        # SE in log space should double when inflator doubles
        # (variance × 4, SD × 2).
        assert p2.forecast_se_log == pytest.approx(2.0 * p1.forecast_se_log, rel=1e-6)


class TestForecastSeLog:
    """Closed-form damped-trend forecast SE tests."""

    def test_zero_residual_zero_se(self):
        assert forecast_se_log(0.0, 6, phi=0.92) == 0.0

    def test_zero_horizon_zero_se(self):
        assert forecast_se_log(0.005, 0, phi=0.92) == 0.0

    def test_se_grows_with_horizon(self):
        s1 = forecast_se_log(0.005, 1, phi=0.92)
        s6 = forecast_se_log(0.005, 6, phi=0.92)
        assert s6 > s1

    def test_phi_one_var_factor_equals_h(self):
        """φ=1 (no damping) → variance factor degenerates to h."""
        s = forecast_se_log(0.01, 5, phi=1.0)
        # var = 0.01² × 5 → SE = 0.01 × sqrt(5)
        expected = 0.01 * (5 ** 0.5)
        assert s == pytest.approx(expected, rel=1e-9)
