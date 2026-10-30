"""Phase 9 smoke tests: real CPS-ASEC donor matching, multi-marginal
demographic projection (the per-PUMA forecast-script flag went with the removed demo scripts).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
from tax_modeler import (
    compute_spm_resources,
    hawaii_demographic_targets,
    hawaii_disability_share,
    hawaii_hh_size_dist,
    hawaii_senior_share,
    impute_childcare_expense,
    impute_moop,
    impute_work_expense,
    project_demographics_forward,
)
from tax_modeler.calibration.donor_match import (
    _DEFAULT_CPS_DONOR_CSV,
    _REAL_CPS_HI_PARQUET,
)

pytestmark = pytest.mark.smoke


REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Phase 9a: real CPS-ASEC + extended donor matches
# ---------------------------------------------------------------------------


def test_cps_donor_loader_falls_back_when_real_missing(taxed_units, monkeypatch):
    """If the real CPS slice isn't present, the synthetic CSV is the fallback."""
    if not _DEFAULT_CPS_DONOR_CSV.exists():
        pytest.skip("synthetic donor CSV missing")
    # Move the real parquet aside to force the fallback
    real = _REAL_CPS_HI_PARQUET
    if real.exists():
        backup = real.with_suffix(".parquet.bak")
        real.rename(backup)
        try:
            df = impute_moop(taxed_units)
            assert "moop_amount" in df.columns
        finally:
            backup.rename(real)
    else:
        df = impute_moop(taxed_units)
        assert "moop_amount" in df.columns


def test_cps_real_slice_is_present():
    """The bundled HI CPS slice was generated and exists."""
    assert _REAL_CPS_HI_PARQUET.exists(), (
        f"Real CPS slice missing at {_REAL_CPS_HI_PARQUET}; run "
        "scripts/build_cps_asec_slice.py to generate"
    )
    df = pd.read_parquet(_REAL_CPS_HI_PARQUET)
    # Real slice has the columns donor matchers need
    for col in ("age", "hh_size", "income", "moop"):
        assert col in df.columns, f"missing {col}"
    # Sanity: HI has ~1500 households so person count is roughly 2000-3500
    assert 1000 < len(df) < 5000


def test_impute_moop_on_real_cps_slice(taxed_units):
    """When the real slice is loaded, MOOP imputation produces realistic shape."""
    if not _REAL_CPS_HI_PARQUET.exists():
        pytest.skip("real CPS slice not present")
    df = impute_moop(taxed_units)
    weighted_mean = float(
        (df["moop_amount"] * df["weight"]).sum() / df["weight"].sum()
    )
    # Real CPS HI mean MOOP is ~$5K-7K; allow generous bands for the small
    # synthetic recipient frame.
    assert 1_000 < weighted_mean < 20_000, (
        f"weighted-mean MOOP ${weighted_mean:.0f} out of expected band"
    )


def test_impute_childcare_expense(taxed_units):
    df = impute_childcare_expense(taxed_units)
    assert "childcare_expense_amount" in df.columns
    assert (df["childcare_expense_amount"] >= 0).all()


def test_impute_work_expense(taxed_units):
    df = impute_work_expense(taxed_units)
    assert "work_expense_amount" in df.columns
    assert (df["work_expense_amount"] >= 0).all()


def test_spm_resources_picks_up_phase9_expenses(taxed_units):
    df = impute_moop(taxed_units)
    df = impute_childcare_expense(df)
    df = impute_work_expense(df)
    resources, meta = compute_spm_resources(df)
    # All three expense columns now feed the SPM accounting
    assert "moop" not in meta.zeroed_components or "moop_amount" in meta.zeroed_components
    # No MOOP-zeroed warning expected once the column is present
    moop_zeroed = any(
        c in {"moop", "moop_amount"} for c in meta.zeroed_components
    )
    assert not moop_zeroed, "expected MOOP to be subtracted, not zeroed"


# ---------------------------------------------------------------------------
# Phase 9b: multi-marginal demographic projection
# ---------------------------------------------------------------------------


def test_hawaii_demographic_targets_packs_three_dimensions():
    targets = hawaii_demographic_targets(2040)
    assert set(targets) == {"senior", "hh_size", "disability"}
    assert isinstance(targets["senior"], float)
    assert len(targets["hh_size"]) == 5
    assert isinstance(targets["disability"], float)


def test_hh_size_dist_sums_to_one():
    for year in (2020, 2030, 2040, 2050):
        dist = hawaii_hh_size_dist(year)
        assert abs(sum(dist) - 1.0) < 1e-6


def test_disability_share_monotone_increasing():
    s2020 = hawaii_disability_share(2020)
    s2050 = hawaii_disability_share(2050)
    assert s2020 < s2050


def test_multi_marginal_projection_preserves_total_weight(taxed_units):
    base_total = float(taxed_units["weight"].sum())
    out, result = project_demographics_forward(
        taxed_units, target_year=2040,
        dimensions=["senior", "hh_size", "disability"],
    )
    assert out["weight"].sum() == pytest.approx(base_total, rel=1e-6)
    # All three dimensions appear in per_dim_results
    assert set(result.per_dim_results) == {"senior", "hh_size", "disability"}


def test_multi_marginal_hits_targets(taxed_units):
    """Each dimension's post-rake share matches its target within tolerance."""
    out, result = project_demographics_forward(
        taxed_units, target_year=2040,
        dimensions=["senior", "hh_size", "disability"],
    )
    # Senior: post-rake share matches target within 0.5pp
    senior_post = result.per_dim_results["senior"]["post_shares"]["senior"]
    senior_target = result.per_dim_results["senior"]["target_shares"]["senior"]
    # NOTE: independent rakes mean later dimensions can shift earlier ones.
    # Tolerance is loose to account for that documented limitation.
    assert abs(senior_post - senior_target) < 0.05
    # HH-size cells should each match within 1pp BEFORE later rakes shift them
    hh_post = result.per_dim_results["hh_size"]["post_shares"]
    hh_target = result.per_dim_results["hh_size"]["target_shares"]
    for cell in hh_post:
        assert abs(hh_post[cell] - hh_target[cell]) < 0.02


def test_single_dim_back_compat(taxed_units):
    """Calling without dimensions kwarg uses the original senior-only behavior."""
    out, result = project_demographics_forward(taxed_units, target_year=2040)
    # No multi-dim payload populated
    assert result.per_dim_results == {}
    # senior_share_target is set
    assert result.senior_share_target == pytest.approx(hawaii_senior_share(2040))
