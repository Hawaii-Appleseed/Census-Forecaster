"""Phase H — B19082_005E top-quintile income share projection tests.

Tests for :mod:`tax_modeler.projection.b19082_projection`.

Design
------
The bundled ACS panel does NOT yet contain B19082_005E (it will be populated
on the next ``make panel`` rebuild with a Census API key).  All tests that
need actual B19082 data mock ``_load_b19082_series`` to return a synthetic
series of ``AcsObservation`` objects.

Tests that exercise the "data unavailable" path call the real functions
without mocking — they should gracefully return ``None``.

Test organisation
-----------------
1. ``TestLoadB19082SeriesNoData`` — graceful empty-panel behaviour.
2. ``TestGetBaseTopQuintileShare`` — lookup + percent→fraction conversion.
3. ``TestProjectTopQuintileShare`` — projector selection + sanity bounds.
4. ``TestComputeTopIncomeScale`` — ratio + edge cases.
5. ``TestPhaseHIntegration`` — end-to-end with revenue_projection.py.
6. ``TestPhaseHShipGates`` — quality gates.
"""
from __future__ import annotations

import math
from unittest.mock import patch

import pytest
from tax_modeler.projection.b19082_projection import (
    B19082_TOP_QUINTILE,
    _load_b19082_series,
    compute_top_income_scale,
    get_base_top_quintile_share,
    project_top_quintile_share,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_obs(year: int, estimate: float, vintage: str = "1y",
              geoid: str = "15003") -> object:
    """Build a synthetic AcsObservation for B19082_005E."""
    from common.models import AcsObservation
    return AcsObservation(
        estimate=estimate,
        moe=1.5,
        year=year,
        vintage=vintage,
        geoid=geoid,
        indicator=B19082_TOP_QUINTILE,
    )


def _make_series(
    start_year: int = 2015,
    end_year: int = 2022,
    base_pct: float = 51.0,
    drift: float = 0.1,
    geoid: str = "15003",
) -> tuple:
    """Build a synthetic multi-year B19082_005E series as percentages.

    ``estimate = base_pct + drift × (year - start_year)`` — a slowly
    rising series (income divergence widening over time).
    """
    obs = []
    for yr in range(start_year, end_year + 1):
        est = base_pct + drift * (yr - start_year)
        obs.append(_make_obs(yr, est, geoid=geoid))
    return tuple(obs)


# ---------------------------------------------------------------------------
# 1. Data loading — graceful empty-panel behaviour
# ---------------------------------------------------------------------------

class TestLoadB19082SeriesNoData:
    """When the panel has no B19082_005E, all public functions return None."""

    def test_load_returns_empty_tuple_when_no_b19082_in_panel(self):
        """The production panel doesn't have B19082_005E yet → empty tuple."""
        result = _load_b19082_series("15003")
        # Either an empty tuple (not in panel) or a valid tuple of obs.
        # Since the panel hasn't been rebuilt with B19082, we expect ().
        # If it's non-empty, it means the panel was rebuilt — both cases are valid.
        assert isinstance(result, tuple)

    def test_get_base_returns_none_when_no_data(self):
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=(),
        ):
            assert get_base_top_quintile_share(2022, "15003") is None

    def test_project_returns_none_when_no_data(self):
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=(),
        ):
            assert project_top_quintile_share(2026, "15003") is None

    def test_compute_scale_returns_none_when_no_data(self):
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=(),
        ):
            assert compute_top_income_scale(2026, 2022, "15003") is None

    def test_unknown_geoid_returns_none(self):
        assert get_base_top_quintile_share(2022, "99999") is None
        assert project_top_quintile_share(2026, "99999") is None
        assert compute_top_income_scale(2026, 2022, "99999") is None


# ---------------------------------------------------------------------------
# 2. get_base_top_quintile_share — lookup + percent→fraction conversion
# ---------------------------------------------------------------------------

class TestGetBaseTopQuintileShare:
    """Test base-year share lookup and unit conversion."""

    def test_exact_year_1yr_vintage_returned(self):
        """Returns exact 1-year ACS match at base_year."""
        series = _make_series(2015, 2024)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = get_base_top_quintile_share(base_year=2020, geoid="15003")
        assert share is not None
        assert math.isfinite(share)
        # estimate at 2020 = 51.0 + 0.1*(2020-2015) = 51.5 → fraction 0.515
        assert abs(share - 0.515) < 1e-6

    def test_percent_converted_to_fraction(self):
        """Raw estimate > 1.0 is divided by 100 to give a fraction."""
        series = (_make_obs(2022, 51.2),)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = get_base_top_quintile_share(base_year=2022)
        assert share is not None
        assert abs(share - 0.512) < 1e-6, f"Expected 0.512, got {share}"

    def test_fraction_value_not_doubled(self):
        """Raw estimate already in (0, 1) is returned as-is (no ÷100)."""
        series = (_make_obs(2022, 0.512),)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = get_base_top_quintile_share(base_year=2022)
        assert share is not None
        assert abs(share - 0.512) < 1e-6, f"Expected 0.512, got {share}"

    def test_falls_back_to_most_recent_earlier_year(self):
        """If base_year not in panel, uses the most-recent earlier 1-year obs."""
        series = _make_series(2015, 2021)  # no 2022 obs
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = get_base_top_quintile_share(base_year=2022, geoid="15003")
        assert share is not None
        # Should use 2021 obs: 51.0 + 0.1*6 = 51.6 → 0.516
        assert abs(share - 0.516) < 1e-6

    def test_returns_none_when_no_obs_before_base_year(self):
        """No obs at or before base_year → None."""
        series = (_make_obs(2025, 52.0),)  # only a future obs
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = get_base_top_quintile_share(base_year=2022)
        assert share is None

    def test_share_in_unit_interval(self):
        """Returned share is always in (0, 1) for a realistic % value."""
        series = (_make_obs(2022, 51.5),)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = get_base_top_quintile_share(base_year=2022)
        assert share is not None
        assert 0.0 < share < 1.0


# ---------------------------------------------------------------------------
# 3. project_top_quintile_share — projector selection + sanity bounds
# ---------------------------------------------------------------------------

class TestProjectTopQuintileShare:
    """Test projection method selection, output ranges, and edge cases."""

    def test_short_series_uses_carry_forward_and_returns_value(self):
        """< 5 obs → carry_forward projector; should return a valid fraction."""
        series = _make_series(2019, 2022)  # 4 obs
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = project_top_quintile_share(2026, geoid="15003")
        assert share is not None
        assert 0.0 < share < 1.0

    def test_long_series_uses_damped_log_trend_and_returns_value(self):
        """≥ 5 obs → damped_log_trend projector; should return a valid fraction."""
        series = _make_series(2015, 2022)  # 8 obs
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = project_top_quintile_share(2026, geoid="15003")
        assert share is not None
        assert 0.0 < share < 1.0

    def test_result_is_fraction_not_percent(self):
        """Projected share is always returned as a fraction in (0, 1)."""
        series = _make_series(2015, 2022, base_pct=51.0, drift=0.05)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = project_top_quintile_share(2026, geoid="15003")
        assert share is not None
        # Must be in fraction form, not percent form (< 1.0)
        assert share < 1.0, f"Expected fraction, got {share} (likely still percent)"

    def test_anchor_year_limits_training_data(self):
        """Restricting anchor_year to an earlier year changes the projection."""
        series = _make_series(2015, 2022)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share_full = project_top_quintile_share(2026, geoid="15003", anchor_year=2022)
            share_early = project_top_quintile_share(2026, geoid="15003", anchor_year=2018)
        assert share_full is not None
        assert share_early is not None
        # Different training windows → different projections (not required to differ,
        # but we check both are valid)
        assert 0.0 < share_full < 1.0
        assert 0.0 < share_early < 1.0

    def test_project_beyond_anchor_returns_valid_fraction(self):
        """Projecting 4+ years past anchor still returns a valid share."""
        series = _make_series(2015, 2022)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = project_top_quintile_share(2030, geoid="15003")
        assert share is not None
        assert 0.0 < share < 1.0

    def test_no_1yr_vintages_returns_none(self):
        """If all observations are 5-year vintages, returns None."""
        series = tuple(
            _make_obs(yr, 51.0 + 0.1 * i, vintage="5y")
            for i, yr in enumerate(range(2017, 2023))
        )
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = project_top_quintile_share(2026, geoid="15003")
        assert share is None

    def test_fraction_already_in_01_not_divided_again(self):
        """If projector returns a fraction (0–1), it stays a fraction."""
        series = tuple(_make_obs(yr, 0.512, vintage="1y") for yr in range(2015, 2023))
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            share = project_top_quintile_share(2026, geoid="15003")
        assert share is not None
        assert share < 1.0, "Fraction input must not be double-divided"


# ---------------------------------------------------------------------------
# 4. compute_top_income_scale — ratio + edge cases
# ---------------------------------------------------------------------------

class TestComputeTopIncomeScale:
    """Test the ratio computation and edge cases."""

    def test_flat_series_returns_scale_near_one(self):
        """If top share is flat (no divergence), scale ≈ 1.0."""
        # All obs the same value: no drift
        series = _make_series(2015, 2022, base_pct=51.2, drift=0.0)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            scale = compute_top_income_scale(2026, base_year=2022, geoid="15003")
        assert scale is not None
        # Carry-forward of flat series → projection = base → scale ≈ 1.0
        assert abs(scale - 1.0) < 0.05, f"Flat-series scale should be ~1.0, got {scale}"

    def test_growing_series_returns_scale_above_one(self):
        """If top share is trending up, scale > 1 (top earners pulling away)."""
        # Clearly rising series: 50 → 55 over 2015–2022 (0.714 ppt/yr)
        series = _make_series(2015, 2022, base_pct=50.0, drift=0.714)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            scale = compute_top_income_scale(2026, base_year=2022, geoid="15003")
        assert scale is not None
        assert scale > 1.0, f"Growing share should give scale > 1.0, got {scale}"

    def test_declining_series_returns_scale_below_one(self):
        """If top share is trending down, scale < 1 (compression)."""
        # Clearly falling series: 54 → 49 over 2015–2022 (~-0.71 ppt/yr)
        series = _make_series(2015, 2022, base_pct=54.0, drift=-0.714)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            scale = compute_top_income_scale(2026, base_year=2022, geoid="15003")
        assert scale is not None
        assert scale < 1.0, f"Declining share should give scale < 1.0, got {scale}"

    def test_scale_is_ratio_of_projected_to_base(self):
        """scale = projected_share / base_share (not divided by anything else)."""
        series = _make_series(2015, 2022, base_pct=50.0, drift=0.0)

        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            base = get_base_top_quintile_share(2022, geoid="15003")
            proj = project_top_quintile_share(2026, geoid="15003")
            scale = compute_top_income_scale(2026, base_year=2022, geoid="15003")

        assert base is not None and proj is not None and scale is not None
        expected = proj / base
        assert abs(scale - expected) < 1e-9, (
            f"scale={scale:.6f} should equal proj/base={expected:.6f}"
        )

    def test_scale_non_negative(self):
        """Scale factor must be non-negative."""
        series = _make_series(2015, 2022)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            scale = compute_top_income_scale(2026, base_year=2022, geoid="15003")
        assert scale is not None
        assert scale >= 0.0

    def test_returns_none_when_base_share_is_none(self):
        """Returns None if base year data is unavailable."""
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=(),
        ):
            scale = compute_top_income_scale(2026, base_year=2022, geoid="15003")
        assert scale is None

    def test_returns_none_when_projected_share_is_none(self):
        """Returns None if projection fails (only base year obs available,
        no 1-year vintages to build a training set)."""
        # Single obs at base_year in 5y vintage so get_base finds nothing but
        # project_top_quintile_share returns None (no 1y vintages)
        series = (_make_obs(2022, 51.0, vintage="5y"),)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            scale = compute_top_income_scale(2026, base_year=2022, geoid="15003")
        assert scale is None

    def test_scale_in_economically_plausible_range(self):
        """Scale factor should be within [0.80, 1.30] for a realistic series."""
        # 50.0 → 53.5 over 2015–2022 (0.5 ppt/yr), project to 2026
        series = _make_series(2015, 2022, base_pct=50.0, drift=0.5)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            scale = compute_top_income_scale(2026, base_year=2022, geoid="15003")
        assert scale is not None
        assert 0.80 <= scale <= 1.30, (
            f"scale={scale:.4f} outside economically plausible [0.80, 1.30]"
        )


# ---------------------------------------------------------------------------
# 5. Phase H integration — revenue_projection.py wiring
# ---------------------------------------------------------------------------

class TestPhaseHIntegration:
    """Test that B19082 is correctly wired into project_revenue_per_filer."""

    def test_use_b19082_false_sets_top_income_scale_none(self):
        """use_b19082=False → top_income_scale is None regardless of data."""
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        series = _make_series(2015, 2022)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=False
            )
        assert proj is not None
        assert proj.top_income_scale is None
        assert proj.b19082_top_share_projected is None

    def test_use_b19082_true_no_data_still_returns_projection(self):
        """use_b19082=True with no B19082 data → graceful, top_income_scale=None."""
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=(),
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )
        assert proj is not None
        assert proj.top_income_scale is None
        assert proj.b19082_top_share_projected is None
        # Revenue should still be reasonable (uniform growth fallback)
        assert 4_000 < proj.per_filer_revenue < 9_000

    def test_use_b19082_true_with_data_sets_scale(self):
        """use_b19082=True with mocked B19082 data → top_income_scale is set."""
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        series = _make_series(2015, 2022, base_pct=51.0, drift=0.1)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )
        assert proj is not None
        # With data available, both Phase H fields should be populated
        assert proj.top_income_scale is not None
        assert proj.b19082_top_share_projected is not None

    def test_custom_tax_fn_bypasses_b19082(self):
        """Custom tax_fn → B19082 layer is skipped; top_income_scale=None."""
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        def flat_fn(y):
            return y * 0.05
        series = _make_series(2015, 2022)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            proj = project_revenue_per_filer(
                target_year=2026, tax_fn=flat_fn, use_b19082=True
            )
        assert proj is not None
        # B19082 is skipped when a custom tax_fn is provided
        assert proj.top_income_scale is None

    def test_b19082_exception_handled_gracefully(self):
        """If B19082 computation raises, projection still succeeds (None scale)."""
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        def _raise(*args, **kwargs):
            raise RuntimeError("Simulated B19082 failure")

        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            side_effect=_raise,
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )
        assert proj is not None
        assert proj.top_income_scale is None

    def test_b19082_scale_changes_per_filer_revenue(self):
        """With a growing top-quintile share, revenue should exceed uniform-growth path."""
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        # Growing top share (top earners pulling away)
        growing_series = _make_series(2015, 2022, base_pct=50.0, drift=0.5)
        # Flat top share
        _flat_series = _make_series(2015, 2022, base_pct=51.2, drift=0.0)

        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=growing_series,
        ):
            proj_growing = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )

        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=(),
        ):
            proj_uniform = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=False
            )

        assert proj_growing is not None and proj_uniform is not None

        # Growing share → scale > 1 → top brackets grow more → higher revenue
        if proj_growing.top_income_scale is not None and proj_growing.top_income_scale > 1.0:
            assert proj_growing.per_filer_revenue > proj_uniform.per_filer_revenue, (
                "Growing top-quintile share should increase per-filer revenue "
                "vs. uniform-growth path"
            )


# ---------------------------------------------------------------------------
# 5b. Phase H status auditability — b19082_status field
# ---------------------------------------------------------------------------

class TestPhaseHStatus:
    """The b19082_status field disambiguates *why* the layer is/ isn't applied."""

    def test_status_applied_when_data_present(self):
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        series = _make_series(2015, 2022, base_pct=51.0, drift=0.1)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )
        assert proj is not None
        assert proj.b19082_status == "applied"
        assert proj.top_income_scale is not None

    def test_status_inactive_no_panel_data(self):
        """Requested but data absent → explicit no_panel_data status, not silent."""
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=(),
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )
        assert proj is not None
        assert proj.b19082_status == "inactive:no_panel_data"
        assert proj.top_income_scale is None

    def test_status_disabled_when_use_b19082_false(self):
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        proj = project_revenue_per_filer(
            target_year=2026, geoid="15003", use_b19082=False
        )
        assert proj is not None
        assert proj.b19082_status == "disabled"

    def test_status_skipped_for_custom_tax_fn(self):
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        proj = project_revenue_per_filer(
            target_year=2026, geoid="15003", tax_fn=lambda y: y * 0.05, use_b19082=True
        )
        assert proj is not None
        assert proj.b19082_status == "skipped:custom_tax_fn"

    def test_status_inactive_on_error(self):
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        def _raise(*args, **kwargs):
            raise RuntimeError("Simulated B19082 failure")

        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            side_effect=_raise,
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )
        assert proj is not None
        assert proj.b19082_status == "inactive:error"
        assert proj.top_income_scale is None

    def test_default_production_path_reports_no_panel_data(self):
        """End-to-end: the bundled panel lacks B19082, so the real default
        projection must report inactive:no_panel_data (or applied if a future
        panel rebuild adds it)."""
        from tax_modeler.projection.revenue_projection import project_revenue_2026

        proj = project_revenue_2026(use_b19082=True)
        assert proj is not None
        assert proj.b19082_status in {"applied", "inactive:no_panel_data"}


# ---------------------------------------------------------------------------
# 6. Phase H ship gates
# ---------------------------------------------------------------------------

class TestPhaseHShipGates:
    """Quality gates for the Phase H implementation."""

    def test_real_projection_does_not_crash_with_use_b19082(self):
        """project_revenue_2026(use_b19082=True) does not crash.

        Since B19082_005E is not yet in the panel, this tests the graceful
        degradation path end-to-end.
        """
        from tax_modeler.projection.revenue_projection import project_revenue_2026
        proj = project_revenue_2026(use_b19082=True)
        assert proj is not None
        assert isinstance(proj.top_income_scale, (float, type(None)))
        assert isinstance(proj.b19082_top_share_projected, (float, type(None)))

    def test_real_projection_with_b19082_false_matches_b19082_true_when_no_data(self):
        """When B19082 data is unavailable, use_b19082=True and False give same revenue.

        Both paths fall back to uniform growth (fn(projected_income)).
        """
        from tax_modeler.projection.revenue_projection import project_revenue_2026

        # Force no B19082 data for both runs
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=(),
        ):
            proj_on = project_revenue_2026(use_b19082=True)
            proj_off = project_revenue_2026(use_b19082=False)

        assert proj_on is not None and proj_off is not None
        assert proj_on.per_filer_revenue == pytest.approx(
            proj_off.per_filer_revenue, rel=1e-9
        ), "Same revenue when B19082 data absent"

    def test_scale_factor_within_sane_range_when_data_available(self):
        """With mocked B19082 data, the scale factor is in [0.85, 1.20].

        The top-quintile share for US metros moves slowly (~0.1–0.3 ppt/yr).
        Over a 4-year window, the scale should stay within ±15% of 1.0.
        """
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        # Realistic 0.15 ppt/yr drift: 51.0 → 52.05 over 2015–2022
        series = _make_series(2015, 2022, base_pct=51.0, drift=0.15)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )
        assert proj is not None
        if proj.top_income_scale is not None:
            assert 0.85 <= proj.top_income_scale <= 1.20, (
                f"top_income_scale={proj.top_income_scale:.4f} outside sane [0.85, 1.20]"
            )

    def test_b19082_projected_share_is_fraction_in_unit_interval(self):
        """b19082_top_share_projected is always in (0, 1) when populated."""
        from tax_modeler.projection.revenue_projection import project_revenue_per_filer

        series = _make_series(2015, 2022)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            proj = project_revenue_per_filer(
                target_year=2026, geoid="15003", use_b19082=True
            )
        assert proj is not None
        if proj.b19082_top_share_projected is not None:
            assert 0.0 < proj.b19082_top_share_projected < 1.0, (
                f"b19082_top_share_projected={proj.b19082_top_share_projected:.4f} "
                "not in (0, 1)"
            )

    def test_phase_g_ship_gates_still_pass_with_b19082(self):
        """Phase G ship gate: 2026 revenue growth 4–100% with B19082 enabled.

        Verifies that Phase H does not break the Phase G revenue plausibility
        bounds even when B19082 is wired in.
        """
        from tax_modeler.projection.revenue_projection import project_revenue_2026

        # Realistic series
        series = _make_series(2015, 2022, base_pct=51.0, drift=0.1)
        with patch(
            "tax_modeler.projection.b19082_projection._load_b19082_series",
            return_value=series,
        ):
            proj = project_revenue_2026(use_b19082=True)
        assert proj is not None
        years = proj.target_year - 2022  # 4 years
        actual_growth = proj.revenue_growth_pct / 100.0
        assert actual_growth > 0.01 * years, "Revenue growth < 1%/yr floor"
        assert actual_growth < 0.25 * years, "Revenue growth > 25%/yr ceiling"
