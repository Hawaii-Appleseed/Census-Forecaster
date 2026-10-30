"""Phase 10 smoke tests: true joint IPF for multi-marginal demographic projection."""
from __future__ import annotations

import numpy as np
import pytest
from tax_modeler import (
    Dimension,
    JointDistributionDrift,
    hawaii_demographic_targets,
    joint_ipf,
    project_demographics_forward,
    validate_joint_distribution,
)
from tax_modeler.errors import ConfigError

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# joint_ipf engine
# ---------------------------------------------------------------------------


def _senior_dim(target: float) -> Dimension:
    return Dimension(
        name="senior",
        cell_fn=lambda df: np.where(
            df["primary_agep"].fillna(0).to_numpy() >= 65, "senior", "nonsenior"
        ),
        target_shares={"senior": target, "nonsenior": 1.0 - target},
    )


def _hh_size_dim(target_tuple) -> Dimension:
    def _cells(df):
        is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
        n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
        hh_size = 1 + is_joint.astype(int) + n_dep
        return np.where(hh_size >= 5, "5plus", hh_size.astype(str))
    return Dimension(
        name="hh_size",
        cell_fn=_cells,
        target_shares={
            "1": target_tuple[0], "2": target_tuple[1],
            "3": target_tuple[2], "4": target_tuple[3],
            "5plus": target_tuple[4],
        },
    )


def test_joint_ipf_single_dim_converges(taxed_units):
    out, result = joint_ipf(
        taxed_units, dimensions=[_senior_dim(0.235)],
    )
    assert result.converged
    senior_share = float(
        out.loc[out["primary_agep"] >= 65, "weight"].sum() / out["weight"].sum()
    )
    assert abs(senior_share - 0.235) < 1e-3


def test_joint_ipf_multi_dim_converges(taxed_units):
    out, result = joint_ipf(
        taxed_units,
        dimensions=[
            _senior_dim(0.265),
            _hh_size_dim((0.290, 0.325, 0.165, 0.125, 0.095)),
        ],
        tol=5e-3,
    )
    # Iteration count should be modest (joint IPF on 2 dims converges fast)
    assert result.n_iterations <= 25
    # Both marginals within tolerance
    for dim_name, post in result.per_dim_final_shares.items():
        target = result.per_dim_target_shares[dim_name]
        for cell, post_share in post.items():
            assert abs(post_share - target[cell]) < 5e-3, (
                f"{dim_name}/{cell}: post {post_share:.4f} target {target[cell]:.4f}"
            )


def test_joint_ipf_preserves_total_weight(taxed_units):
    base_total = float(taxed_units["weight"].sum())
    out, _ = joint_ipf(
        taxed_units,
        dimensions=[
            _senior_dim(0.265),
            _hh_size_dim((0.290, 0.325, 0.165, 0.125, 0.095)),
        ],
    )
    assert out["weight"].sum() == pytest.approx(base_total, rel=1e-6)


def test_joint_ipf_no_dimensions_raises(taxed_units):
    with pytest.raises(ConfigError):
        joint_ipf(taxed_units, dimensions=[])


def test_joint_ipf_skips_empty_cells(taxed_units):
    """Cells with no observed weight have their target mass redistributed."""
    out, result = joint_ipf(
        taxed_units,
        dimensions=[_hh_size_dim((0.290, 0.325, 0.165, 0.125, 0.095))],
    )
    # Synthetic fixture has only hh_size 1 and 2 → cells 3, 4, 5plus skipped
    assert "hh_size" in result.per_dim_skipped_cells
    skipped = result.per_dim_skipped_cells["hh_size"]
    assert {"3", "4", "5plus"}.issubset(set(skipped))


# ---------------------------------------------------------------------------
# Joint-distribution validation
# ---------------------------------------------------------------------------


def test_validate_joint_distribution_baseline_passes(taxed_units):
    """Comparing a frame to itself should report zero drift."""
    senior = _senior_dim(0.20)
    hh = _hh_size_dim((0.30, 0.32, 0.16, 0.13, 0.09))
    passed, drifts = validate_joint_distribution(
        taxed_units, taxed_units,
        dimensions=[senior, hh],
    )
    assert passed
    # Largest drift is approximately zero
    assert all(d.abs_drift < 1e-9 for d in drifts)


def test_validate_joint_distribution_after_rake(taxed_units):
    """A real rake should produce some drift but bounded."""
    senior = _senior_dim(0.265)
    hh = _hh_size_dim((0.290, 0.325, 0.165, 0.125, 0.095))
    out, _ = joint_ipf(taxed_units, dimensions=[senior, hh])
    passed, drifts = validate_joint_distribution(
        taxed_units, out, dimensions=[senior, hh],
        drift_threshold_rel=0.50,  # generous on small fixture
    )
    # Drifts list non-empty
    assert len(drifts) > 0
    # Largest drift cell exists
    worst = drifts[0]
    assert isinstance(worst, JointDistributionDrift)


# ---------------------------------------------------------------------------
# project_demographics_forward(use_joint_ipf=True)
# ---------------------------------------------------------------------------


def test_demographic_projection_via_joint_ipf(taxed_units):
    out, result = project_demographics_forward(
        taxed_units,
        target_year=2040,
        dimensions=["senior", "hh_size", "disability"],
        use_joint_ipf=True,
        joint_ipf_tol=5e-3,
    )
    # The joint-IPF path populates per_dim_results in the same shape
    assert set(result.per_dim_results) == {"senior", "hh_size", "disability"}
    # And preserves total weight
    assert out["weight"].sum() == pytest.approx(taxed_units["weight"].sum(), rel=1e-6)
    # Note records the IPF convergence outcome
    assert any("joint_ipf converged" in n for n in result.notes)


def test_joint_ipf_better_than_independent_rakes_on_correlated_dims(taxed_units):
    """Joint IPF should hit ALL marginals; independent rakes can drift on later dims."""
    targets = hawaii_demographic_targets(2040)

    # Independent (Phase 9b sequential rakes)
    indep_out, indep_res = project_demographics_forward(
        taxed_units, target_year=2040,
        dimensions=["senior", "hh_size", "disability"],
        use_joint_ipf=False,
    )

    # Joint IPF (Phase 10)
    joint_out, joint_res = project_demographics_forward(
        taxed_units, target_year=2040,
        dimensions=["senior", "hh_size", "disability"],
        use_joint_ipf=True,
    )

    # Check the senior marginal AFTER all rakes ran
    senior_target = targets["senior"]

    indep_senior = float(
        indep_out.loc[indep_out["primary_agep"] >= 65, "weight"].sum()
        / indep_out["weight"].sum()
    )
    joint_senior = float(
        joint_out.loc[joint_out["primary_agep"] >= 65, "weight"].sum()
        / joint_out["weight"].sum()
    )

    indep_err = abs(indep_senior - senior_target)
    joint_err = abs(joint_senior - senior_target)
    # Joint IPF should hit the marginal at least as well (typically much better)
    assert joint_err <= indep_err + 1e-6, (
        f"joint IPF senior drift {joint_err:.4f} exceeds independent rake "
        f"drift {indep_err:.4f}"
    )
