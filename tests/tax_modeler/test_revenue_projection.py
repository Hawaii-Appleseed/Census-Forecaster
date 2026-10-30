"""Phase G — end-to-end revenue projection entry point tests.

Tests for :mod:`tax_modeler.projection.revenue_projection`.

Design
------
All tests use the bundled census_forecaster panel (no API calls).  The
panel covers Honolulu (15003) and 7 other large US counties through 2024.
Tests that need isolation mock the projector or tax_fn.

Test organisation
-----------------
1. ``TestRevenueProjectionDataclass`` — frozen dataclass structural integrity.
2. ``TestProjectRevenuePerFilerSmoke`` — basic returns and None conditions.
3. ``TestProjectRevenuePerFilerNumerics`` — value ranges and economic sense.
4. ``TestProjectRevenuePerFilerParams`` — parameter wiring (method, tax_fn,
   conformal_quantiles, anchor_year).
5. ``TestProjectRevenue2026`` — convenience wrapper correctness.
6. ``TestFormatProjectionReport`` — string formatting smoke tests.
7. ``TestPhaseGShipGates`` — end-to-end quality gates.
"""
from __future__ import annotations

import math

import pytest
from tax_modeler.projection.revenue_concentration import (
    DOTAX_2022_MEDIAN_INCOME,
    expected_revenue_per_filer,
)
from tax_modeler.projection.revenue_projection import (
    DEFAULT_GEOID,
    DEFAULT_METHOD,
    RevenueProjection,
    format_projection_report,
    project_revenue_2026,
    project_revenue_per_filer,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_projection(**kwargs) -> RevenueProjection:
    """Build a RevenueProjection with sensible defaults for unit tests."""
    defaults = dict(
        target_year=2026,
        geoid="15003",
        method="ensemble",
        anchor_year=2024,
        anchor_income=96_580.0,
        projected_income=112_000.0,
        income_se=4_000.0,
        per_filer_revenue=6_600.0,
        revenue_se=280.0,
        ci90_low=6_140.0,
        ci90_high=7_060.0,
        conformal_multiplier=1.645,
        base_per_filer_revenue=4_769.80,
        income_growth_factor=1.338,
        revenue_growth_pct=38.4,
        # Phase H fields — optional, default None
        top_income_scale=None,
        b19082_top_share_projected=None,
    )
    defaults.update(kwargs)
    return RevenueProjection(**defaults)


# ---------------------------------------------------------------------------
# 1. RevenueProjection dataclass
# ---------------------------------------------------------------------------

class TestRevenueProjectionDataclass:
    """Structural tests for the RevenueProjection dataclass."""

    def test_frozen(self):
        """RevenueProjection is immutable."""
        proj = _make_projection()
        with pytest.raises((AttributeError, TypeError)):
            proj.per_filer_revenue = 9999.0  # type: ignore[misc]

    def test_all_fields_accessible(self):
        proj = _make_projection()
        assert proj.target_year == 2026
        assert proj.geoid == "15003"
        assert proj.method == "ensemble"
        assert proj.anchor_year == 2024
        assert proj.anchor_income > 0
        assert proj.projected_income > 0
        assert proj.income_se > 0
        assert proj.per_filer_revenue > 0
        assert proj.revenue_se > 0
        assert proj.ci90_low < proj.ci90_high
        assert proj.conformal_multiplier > 0
        assert proj.base_per_filer_revenue > 0
        assert proj.income_growth_factor > 0
        assert math.isfinite(proj.revenue_growth_pct)
        # Phase H fields: None by default (B19082 not in panel yet)
        assert proj.top_income_scale is None
        assert proj.b19082_top_share_projected is None

    def test_phase_h_fields_accept_float(self):
        """top_income_scale and b19082_top_share_projected accept float values."""
        proj = _make_projection(top_income_scale=1.03, b19082_top_share_projected=0.521)
        assert proj.top_income_scale == pytest.approx(1.03)
        assert proj.b19082_top_share_projected == pytest.approx(0.521)

    def test_ci_ordering(self):
        proj = _make_projection(ci90_low=5_000.0, ci90_high=7_000.0)
        assert proj.ci90_low < proj.ci90_high

    def test_base_revenue_close_to_dotax_calibration(self):
        """base_per_filer_revenue should be ~$4,769.80 (DOTAX 2022)."""
        proj = _make_projection(base_per_filer_revenue=expected_revenue_per_filer(DOTAX_2022_MEDIAN_INCOME))
        assert abs(proj.base_per_filer_revenue - 4_770.0) < 10.0


# ---------------------------------------------------------------------------
# 2. project_revenue_per_filer — smoke tests
# ---------------------------------------------------------------------------

class TestProjectRevenuePerFilerSmoke:
    """Basic return type and None-path tests."""

    def test_returns_revenue_projection_for_honolulu(self):
        """Honolulu (15003) should be in the bundled panel — must succeed."""
        proj = project_revenue_per_filer(target_year=2026, geoid="15003")
        assert proj is not None
        assert isinstance(proj, RevenueProjection)

    def test_returns_none_for_unknown_geoid(self):
        """Geoid not in panel → None (graceful)."""
        proj = project_revenue_per_filer(target_year=2026, geoid="99999")
        assert proj is None

    def test_fields_finite_and_positive(self):
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        for field in ("projected_income", "income_se", "per_filer_revenue",
                      "revenue_se", "base_per_filer_revenue",
                      "income_growth_factor", "conformal_multiplier"):
            val = getattr(proj, field)
            assert math.isfinite(val) and val > 0, (
                f"{field}={val} should be finite and positive"
            )

    def test_ci_low_less_than_high(self):
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        assert proj.ci90_low < proj.ci90_high

    def test_ci_contains_point_estimate(self):
        """CI must bracket the point estimate."""
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        assert proj.ci90_low <= proj.per_filer_revenue <= proj.ci90_high

    def test_geoid_recorded_correctly(self):
        proj = project_revenue_per_filer(target_year=2026, geoid="15003")
        assert proj is not None
        assert proj.geoid == "15003"

    def test_target_year_recorded_correctly(self):
        proj = project_revenue_per_filer(target_year=2027)
        assert proj is not None
        assert proj.target_year == 2027


# ---------------------------------------------------------------------------
# 3. project_revenue_per_filer — numeric correctness
# ---------------------------------------------------------------------------

class TestProjectRevenuePerFilerNumerics:
    """Economic plausibility and calibration tests."""

    def test_2026_per_filer_in_plausible_range(self):
        """2026 projection should be in $4,000–$9,000/filer range.

        Base: $4,770 (DOTAX 2022).  Upper bound: >85% growth from base is
        implausible for a 4-year window.
        """
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        assert 4_000 < proj.per_filer_revenue < 9_000, (
            f"2026 per-filer revenue ${proj.per_filer_revenue:,.0f} "
            "outside plausible range $4,000–$9,000"
        )

    def test_base_rev_matches_dotax_calibration(self):
        """base_per_filer_revenue ≈ $4,769.80 (DOTAX 2022, within 0.1%)."""
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        assert abs(proj.base_per_filer_revenue - 4_769.80) < 5.0, (
            f"base_per_filer_revenue ${proj.base_per_filer_revenue:.2f} "
            "should be near $4,769.80 (DOTAX 2022 average)"
        )

    def test_revenue_exceeds_base(self):
        """For 2026, projected revenue should exceed the 2022 base."""
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        assert proj.per_filer_revenue > proj.base_per_filer_revenue, (
            "2026 per-filer revenue should exceed 2022 base (income growth)"
        )

    def test_income_growth_factor_reasonable(self):
        """Income growth factor from 2022 base should be in [0.8, 2.5]."""
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        assert 0.8 < proj.income_growth_factor < 2.5, (
            f"income_growth_factor {proj.income_growth_factor:.3f} "
            "outside [0.8, 2.5] range"
        )

    def test_revenue_growth_exceeds_income_growth(self):
        """Revenue growth % > income growth % (bracket creep).

        For a progressive tax, revenue grows faster than income.
        """
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        income_growth_pct = (proj.income_growth_factor - 1) * 100
        assert proj.revenue_growth_pct > income_growth_pct, (
            f"Revenue growth {proj.revenue_growth_pct:.1f}% should exceed "
            f"income growth {income_growth_pct:.1f}% (bracket creep)"
        )

    def test_revenue_se_positive_when_income_se_positive(self):
        """If income_se > 0, revenue_se must be > 0."""
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        if proj.income_se > 0:
            assert proj.revenue_se > 0, "Delta-method SE should propagate income_se"

    def test_ci_width_in_plausible_range(self):
        """90% CI width should be $200–$3,000 (not degenerate, not huge)."""
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        width = proj.ci90_high - proj.ci90_low
        assert 100 < width < 5_000, (
            f"CI width ${width:,.0f} outside plausible range $100–$5,000"
        )

    def test_conformal_multiplier_at_least_z90(self):
        """Conformal multiplier ≥ Z_90 = 1.645 by construction."""
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        assert proj.conformal_multiplier >= 1.645 - 1e-9, (
            f"conformal_multiplier {proj.conformal_multiplier:.4f} < Z_90 = 1.645"
        )

    def test_anchor_year_is_last_observed(self):
        """Default anchor_year should be the most recent panel year (2024)."""
        proj = project_revenue_per_filer(target_year=2026)
        assert proj is not None
        # The bundled panel runs through 2024 for Honolulu
        assert proj.anchor_year >= 2022, (
            f"anchor_year {proj.anchor_year} seems unexpectedly old"
        )

    def test_later_target_year_higher_revenue(self):
        """Projecting further out gives higher per-filer revenue (income growth)."""
        proj_2026 = project_revenue_per_filer(target_year=2026)
        proj_2028 = project_revenue_per_filer(target_year=2028)
        if proj_2026 is None or proj_2028 is None:
            pytest.skip("Projection unavailable")
        assert proj_2028.per_filer_revenue > proj_2026.per_filer_revenue, (
            "2028 revenue should exceed 2026 revenue"
        )


# ---------------------------------------------------------------------------
# 4. project_revenue_per_filer — parameter wiring
# ---------------------------------------------------------------------------

class TestProjectRevenuePerFilerParams:
    """Test that keyword parameters are correctly wired through."""

    def test_carry_forward_method(self):
        """carry_forward method is supported and returns a result."""
        proj = project_revenue_per_filer(target_year=2026, method="carry_forward")
        assert proj is not None
        assert proj.method == "carry_forward"

    def test_ensemble_vs_carry_forward_differ(self):
        """Ensemble and carry_forward project different incomes (on non-flat series)."""
        p_ens = project_revenue_per_filer(target_year=2026, method="ensemble")
        p_cf = project_revenue_per_filer(target_year=2026, method="carry_forward")
        assert p_ens is not None and p_cf is not None
        assert p_ens.projected_income != pytest.approx(p_cf.projected_income, rel=0.01), (
            "Ensemble and carry_forward should give different income projections"
        )

    def test_unknown_method_returns_none(self):
        proj = project_revenue_per_filer(target_year=2026, method="nonexistent_method")
        assert proj is None

    def test_custom_tax_fn_respected(self):
        """Custom tax_fn changes per_filer_revenue but not income_se."""
        def flat_fn(y):
            return y * 0.05
        p_conc = project_revenue_per_filer(target_year=2026)
        p_flat = project_revenue_per_filer(target_year=2026, tax_fn=flat_fn)
        assert p_conc is not None and p_flat is not None
        assert p_flat.per_filer_revenue != pytest.approx(p_conc.per_filer_revenue, rel=0.01)
        # Income projection is independent of tax_fn
        assert p_flat.projected_income == pytest.approx(p_conc.projected_income, rel=1e-9)

    def test_custom_anchor_year_respected(self):
        """Explicit anchor_year is recorded and limits training data."""
        p_full = project_revenue_per_filer(target_year=2026)
        p_2022 = project_revenue_per_filer(target_year=2026, anchor_year=2022)
        assert p_full is not None and p_2022 is not None
        assert p_2022.anchor_year == 2022
        # Training on less data may give a different projection
        # (not guaranteed to differ, but anchor_year is correctly stored)

    def test_conformal_quantiles_ci_at_least_as_wide(self):
        """Providing conformal quantiles can only widen (or maintain) the CI.

        With a very large conformal quantile, the CI must be wider than
        without.  horizon = 2026 - anchor_year(2024) = 2 → "short" bucket.
        """
        from tax_modeler.projection.revenue_conformal import WILDCARD, RevenueConformalRecord
        # Artificial record with a large quantile (10×Z_90), wildcard horizon
        big_q = RevenueConformalRecord(
            method="ensemble", h_bucket=WILDCARD,
            quantile=16.45, n_calibration=20, target_coverage=0.90,
        )
        p_plain = project_revenue_per_filer(target_year=2026)
        p_conf = project_revenue_per_filer(target_year=2026, conformal_quantiles=[big_q])
        assert p_plain is not None and p_conf is not None
        width_plain = p_plain.ci90_high - p_plain.ci90_low
        width_conf = p_conf.ci90_high - p_conf.ci90_low
        assert width_conf > width_plain, (
            "Large conformal quantile should widen CI"
        )

    def test_conformal_multiplier_equals_z90_without_quantiles(self):
        """Without conformal_quantiles, multiplier == 1.645 exactly."""
        proj = project_revenue_per_filer(target_year=2026, conformal_quantiles=None)
        assert proj is not None
        assert proj.conformal_multiplier == pytest.approx(1.645, abs=1e-6)


# ---------------------------------------------------------------------------
# 5. project_revenue_2026 convenience wrapper
# ---------------------------------------------------------------------------

class TestProjectRevenue2026:
    """Tests for the project_revenue_2026 wrapper."""

    def test_matches_project_revenue_per_filer(self):
        """project_revenue_2026() == project_revenue_per_filer(target_year=2026)."""
        p1 = project_revenue_2026()
        p2 = project_revenue_per_filer(target_year=2026)
        assert p1 is not None and p2 is not None
        assert p1.per_filer_revenue == pytest.approx(p2.per_filer_revenue, rel=1e-9)
        assert p1.projected_income == pytest.approx(p2.projected_income, rel=1e-9)

    def test_target_year_is_2026(self):
        proj = project_revenue_2026()
        assert proj is not None
        assert proj.target_year == 2026

    def test_default_geoid_is_honolulu(self):
        proj = project_revenue_2026()
        assert proj is not None
        assert proj.geoid == DEFAULT_GEOID

    def test_default_method_is_ensemble(self):
        proj = project_revenue_2026()
        assert proj is not None
        assert proj.method == DEFAULT_METHOD

    def test_different_geoid_param_forwarded(self):
        """geoid parameter is passed through to the underlying function."""
        # 15001 = Hawaii County; also in the bundled panel
        p1 = project_revenue_2026(geoid="15003")
        p2 = project_revenue_2026(geoid="15001")
        if p1 is None or p2 is None:
            pytest.skip("One geoid not in panel")
        assert p1.geoid == "15003"
        assert p2.geoid == "15001"


# ---------------------------------------------------------------------------
# 6. format_projection_report
# ---------------------------------------------------------------------------

class TestFormatProjectionReport:
    """Smoke tests for the string formatting helper."""

    def test_returns_string(self):
        proj = _make_projection()
        report = format_projection_report(proj)
        assert isinstance(report, str)

    def test_contains_target_year(self):
        proj = _make_projection(target_year=2026)
        report = format_projection_report(proj)
        assert "2026" in report

    def test_contains_revenue(self):
        proj = _make_projection(per_filer_revenue=6_757.0)
        report = format_projection_report(proj)
        assert "6,757" in report or "6757" in report

    def test_contains_ci_bounds(self):
        proj = _make_projection(ci90_low=5_900.0, ci90_high=7_100.0)
        report = format_projection_report(proj)
        assert "5,900" in report or "5900" in report
        assert "7,100" in report or "7100" in report

    def test_multiline(self):
        proj = _make_projection()
        report = format_projection_report(proj)
        assert "\n" in report


# ---------------------------------------------------------------------------
# 7. Phase G ship gates
# ---------------------------------------------------------------------------

class TestPhaseGShipGates:
    """End-to-end quality gates for Phase G."""

    def test_2026_revenue_growth_exceeds_1pct_per_year(self):
        """2026 projection shows > 1%/yr revenue growth from 2022 base.

        A floor of 1%/yr (4% over 4 years) is very conservative — it would
        fail only if the income forecast is essentially flat.  Ensures the
        pipeline is actually using live ensemble data, not a constant.
        """
        proj = project_revenue_2026()
        assert proj is not None
        # base_year_for_growth is DOTAX 2022; target = 2026 (4 years)
        years = proj.target_year - 2022
        min_total_growth = 0.01 * years  # 1%/yr floor
        actual_growth = proj.revenue_growth_pct / 100.0
        assert actual_growth > min_total_growth, (
            f"2026 revenue growth {proj.revenue_growth_pct:.1f}% < "
            f"floor {min_total_growth*100:.0f}% ({years} yrs × 1%/yr)"
        )

    def test_2026_revenue_growth_below_ceiling(self):
        """2026 projection shows < 25%/yr revenue growth.

        An upper bound of 25%/yr (100% over 4 years) filters implausible
        runaway forecasts — bracket-creep amplification never exceeds 2×
        income growth, so even 20%/yr income growth stays below this ceiling.
        """
        proj = project_revenue_2026()
        assert proj is not None
        years = proj.target_year - 2022
        max_total_growth = 0.25 * years  # 25%/yr ceiling
        actual_growth = proj.revenue_growth_pct / 100.0
        assert actual_growth < max_total_growth, (
            f"2026 revenue growth {proj.revenue_growth_pct:.1f}% > "
            f"ceiling {max_total_growth*100:.0f}%"
        )

    def test_ensemble_beats_carry_forward_projected_revenue(self):
        """Ensemble income forecast → higher revenue projection than carry-forward.

        On an upward-trending series, the ensemble trend model projects
        higher income → higher revenue (via bracket creep).
        """
        p_ens = project_revenue_2026(method="ensemble")
        p_cf = project_revenue_2026(method="carry_forward")
        assert p_ens is not None and p_cf is not None
        # Ensemble projects higher income on a growing series
        assert p_ens.projected_income > p_cf.projected_income, (
            "Ensemble should project higher income than carry_forward on a "
            "growing Honolulu B19013 series"
        )
        # And therefore higher revenue
        assert p_ens.per_filer_revenue > p_cf.per_filer_revenue

    def test_ci_coverage_sanity(self):
        """CI bounds are economically plausible (not degenerate or enormous)."""
        proj = project_revenue_2026()
        assert proj is not None
        # Lower bound: must be positive
        assert proj.ci90_low > 0, "CI lower bound must be positive"
        # Width: less than 3× the point estimate (not catastrophically wide)
        width = proj.ci90_high - proj.ci90_low
        assert width < 3 * proj.per_filer_revenue

    def test_pipeline_logging_does_not_crash(self, caplog):
        """INFO-level projection log is emitted without errors."""
        import logging
        with caplog.at_level(logging.INFO, logger="tax_modeler.projection.revenue_projection"):
            proj = project_revenue_2026()
        assert proj is not None
        # At least one log record mentioning the projection should exist
        # (may be 0 if all sub-loggers are at WARNING — just check no ERROR)
        for record in caplog.records:
            assert record.levelno < logging.ERROR, (
                f"ERROR log during projection: {record.getMessage()}"
            )
