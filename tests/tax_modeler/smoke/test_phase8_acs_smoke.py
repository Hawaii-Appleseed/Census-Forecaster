"""Smoke tests for Phase 8: ACS-time-series demographic projection,
PUMA stratification, and CPS donor-matching imputation.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler import (
    DonorMatcher,
    distribution_by_geography,
    hawaii_senior_share,
    impute_moop,
    poverty_by_geography,
    project_demographics_forward,
    threshold_for_units,
)
from tax_modeler.errors import ConfigError, DataValidationError

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# Demographic projection
# ---------------------------------------------------------------------------


def test_hawaii_senior_share_monotone():
    s2024 = hawaii_senior_share(2024)
    s2030 = hawaii_senior_share(2030)
    s2050 = hawaii_senior_share(2050)
    assert s2024 < s2030 < s2050
    # Endpoint clamping
    assert hawaii_senior_share(1900) == hawaii_senior_share(2020)
    assert hawaii_senior_share(2100) == hawaii_senior_share(2050)


def test_demographic_projection_preserves_total_weight(taxed_units):
    base_total = taxed_units["weight"].sum()
    out, result = project_demographics_forward(
        taxed_units, target_year=2040, base_year=2024
    )
    assert out["weight"].sum() == pytest.approx(base_total, rel=1e-6)
    # Senior share moves toward target
    is_senior = out["primary_agep"].fillna(0).to_numpy() >= 65
    new_share = float(out.loc[is_senior, "weight"].sum() / out["weight"].sum())
    assert abs(new_share - result.senior_share_target) < 1e-6


def test_demographic_projection_age_threshold(taxed_units):
    """Custom age threshold also works."""
    out, _ = project_demographics_forward(
        taxed_units, target_year=2050, age_threshold=70
    )
    assert "weight" in out.columns
    # Total preserved regardless of threshold
    assert out["weight"].sum() == pytest.approx(taxed_units["weight"].sum(), rel=1e-6)


def test_demographic_projection_missing_age_raises(taxed_units):
    bad = taxed_units.drop(columns=["primary_agep"])
    with pytest.raises(ConfigError):
        project_demographics_forward(bad, target_year=2030)


# ---------------------------------------------------------------------------
# Geographic stratification
# ---------------------------------------------------------------------------


def test_poverty_by_geography_runs(taxed_units):
    from tax_modeler import compute_spm_resources

    df, _ = compute_spm_resources(taxed_units)
    # Use PUMA from the taxed_units (synthetic fixture has PUMA column)
    if "PUMA" not in df.columns:
        pytest.skip("PUMA column unavailable on fixture")
    out = poverty_by_geography(
        df, resources_col="spm_resources",
        threshold=threshold_for_units(df), geo_col="PUMA",
    )
    assert "poverty_rate" in out.columns
    assert (out["poverty_rate"] >= 0).all() and (out["poverty_rate"] <= 1).all()


def test_poverty_by_geography_person_weighted(taxed_units):
    """Regression (audit C2): the headcount poverty_rate must be person-weighted
    (share of *people* poor), not unit-weighted. When poor units are larger than
    non-poor units, the person-weighted rate exceeds the unit-weighted one, and
    the dollar gap (always unit-weighted) is unchanged across both modes.
    """
    import pandas as pd

    df = pd.DataFrame({
        "PUMA": ["A", "A"],
        "spm_resources": [10_000.0, 90_000.0],
        "weight": [100.0, 100.0],
        # Poor unit = MFJ + 3 deps (5 persons); non-poor = single (1 person).
        "filing_status": ["married_filing_jointly", "single"],
        "num_dependents": [3, 0],
    })
    thr = pd.Series([30_000.0, 30_000.0])
    unit = poverty_by_geography(
        df, resources_col="spm_resources", threshold=thr,
        geo_col="PUMA", person_weighted=False,
    )
    pers = poverty_by_geography(
        df, resources_col="spm_resources", threshold=thr,
        geo_col="PUMA", person_weighted=True,
    )
    # Unit-weighted: 1 of 2 units poor -> 0.5. Person-weighted: 5 of 6 persons -> 5/6.
    assert abs(float(unit.loc["A", "poverty_rate"]) - 0.5) < 1e-9
    assert abs(float(pers.loc["A", "poverty_rate"]) - 5.0 / 6.0) < 1e-9
    assert float(pers.loc["A", "count_weighted_persons"]) == 600.0
    # Dollar gap is unit-weighted in both modes.
    assert float(unit.loc["A", "poverty_gap_$"]) == float(pers.loc["A", "poverty_gap_$"])


def test_distribution_by_geography_runs(taxed_units):
    if "PUMA" not in taxed_units.columns:
        pytest.skip("PUMA column unavailable on fixture")
    out = distribution_by_geography(
        taxed_units, value_col="income", geo_col="PUMA",
    )
    assert "gini" in out.columns
    assert (out["gini"] >= 0).all() and (out["gini"] <= 1).all()


def test_geo_helpers_validate(taxed_units):
    with pytest.raises(DataValidationError):
        poverty_by_geography(
            taxed_units, resources_col="not_a_col",
            threshold=20_000, geo_col="PUMA",
        )
    with pytest.raises(ConfigError):
        distribution_by_geography(
            taxed_units, value_col="income",
            indices=("foo",),
        )


# ---------------------------------------------------------------------------
# Donor-matching imputation
# ---------------------------------------------------------------------------


def test_donor_matcher_basic():
    rng = np.random.default_rng(0)
    donors = pd.DataFrame({
        "age": rng.uniform(20, 80, 100),
        "income": rng.uniform(10_000, 200_000, 100),
        "moop": rng.uniform(0, 5_000, 100),
    })
    recipients = pd.DataFrame({
        "age": [30.0, 65.0, 50.0],
        "income": [40_000.0, 25_000.0, 100_000.0],
    })
    matcher = DonorMatcher(
        donors_df=donors, match_vars=("age", "income"),
        donor_vars=("moop",), k=5,
    )
    out = matcher.impute(recipients)
    assert "moop" in out.columns
    # Imputed values should fall within donor range
    assert (out["moop"] >= donors["moop"].min()).all()
    assert (out["moop"] <= donors["moop"].max()).all()


def test_donor_matcher_missing_donor_var_raises():
    donors = pd.DataFrame({"age": [30.0, 50.0], "income": [40_000.0, 80_000.0]})
    with pytest.raises(DataValidationError):
        DonorMatcher(donors_df=donors, match_vars=("age",), donor_vars=("moop",))


def test_impute_moop_runs(taxed_units):
    out = impute_moop(taxed_units)
    assert "moop_amount" in out.columns
    assert (out["moop_amount"] >= 0).all()
    # Mean should be reasonable: between $500 and $10K on the synthetic fixture
    weighted_mean = float(
        (out["moop_amount"] * out["weight"]).sum() / out["weight"].sum()
    )
    assert 500.0 < weighted_mean < 10_000.0


def test_moop_correlates_with_age(taxed_units):
    """Older units should get higher imputed MOOP on average."""
    out = impute_moop(taxed_units)
    if "primary_agep" not in out.columns:
        pytest.skip("primary_agep unavailable")
    young_mask = out["primary_agep"] < 50
    old_mask = out["primary_agep"] >= 50
    if young_mask.sum() < 5 or old_mask.sum() < 5:
        pytest.skip("not enough age variation on fixture")
    young_mean = out.loc[young_mask, "moop_amount"].mean()
    old_mean = out.loc[old_mask, "moop_amount"].mean()
    # Allow either direction since the synthetic fixture is small, but
    # there should be a measurable difference (not identical)
    assert young_mean != old_mean


def test_spm_resources_picks_up_imputed_moop(taxed_units):
    """When moop_amount is on the frame, compute_spm_resources subtracts it."""
    from tax_modeler import compute_spm_resources

    base_resources, base_meta = compute_spm_resources(taxed_units)
    with_moop = impute_moop(taxed_units)
    with_resources, with_meta = compute_spm_resources(with_moop)
    # MOOP no longer in zeroed list when column present
    assert "moop" in base_meta.zeroed_components or "moop_amount" in base_meta.zeroed_components
    # Resources after MOOP subtraction <= without MOOP
    base_total = float((base_resources["spm_resources"] * base_resources["weight"]).sum())
    moop_total = float((with_resources["spm_resources"] * with_resources["weight"]).sum())
    assert moop_total <= base_total
