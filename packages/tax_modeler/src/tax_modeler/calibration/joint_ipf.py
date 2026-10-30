"""Generic multi-dimensional Iterative Proportional Fitting (rake).

Phase 9b shipped multi-marginal demographic projection via *independent
sequential* rakes — one per dimension, no iteration. This works when
dimensions are roughly orthogonal but degrades joint distributions
when dimensions are correlated (e.g., senior status correlates with
household size: seniors are more likely to live alone or in 2-person
households than the population average).

Phase 10 adds **true joint IPF** that cycles over all dimensions and
re-rakes until every marginal converges *simultaneously* to its target.
The classic Deming-Stephan algorithm: starting from the input weights,
repeatedly rake one dimension at a time, looping until all dimension
marginals are within tolerance.

This implementation is generic — it accepts any list of
:class:`Dimension` specs (each with a cell-assignment function + target
shares). The Hawaii demographic-projection use case calls into it
through :func:`tax_modeler.projection.demographic.project_demographics_forward`
with ``use_joint_ipf=True``.

Algorithm (per Lovelace, *spatial-microsim-book*; Beckman et al.,
*JASSS* 1996):

    weights = baseline.copy()
    while not converged(weights, dimensions, tol):
        for dim in dimensions:
            weights = rake_one_dim(weights, dim)
    return weights

Each ``rake_one_dim`` is exactly the post-stratification step Phase 9b
already implements. The wrapper just iterates.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError


@dataclass(frozen=True)
class Dimension:
    """One marginal-rake dimension.

    Attributes
    ----------
    name:
        Human label (used in convergence reports).
    cell_fn:
        Callable ``(units_df) -> array_of_cell_labels`` of length n_rows.
    target_shares:
        Mapping ``cell_label -> share_of_total_weight``. Shares should
        sum to 1.0 (auto-renormalized otherwise).
    """

    name: str
    cell_fn: Callable[[pd.DataFrame], np.ndarray]
    target_shares: Mapping[str, float]


@dataclass
class JointIpfResult:
    """Output of :func:`joint_ipf`.

    Reports per-dimension convergence (final shares vs targets), the
    iteration count, and whether convergence was achieved within
    ``max_iter`` cycles.
    """

    n_iterations: int
    converged: bool
    max_marginal_error: float
    per_dim_final_shares: dict[str, dict[str, float]] = field(default_factory=dict)
    per_dim_target_shares: dict[str, dict[str, float]] = field(default_factory=dict)
    per_dim_skipped_cells: dict[str, list[str]] = field(default_factory=dict)
    notes: tuple = ()


def _rake_to_cells(
    weights: np.ndarray,
    cell_ids: np.ndarray,
    target_shares: Mapping[str, float],
) -> np.ndarray:
    """Single-dimension post-stratification rake. Total weight preserved."""
    total = float(weights.sum())
    if total == 0:
        return weights.copy()
    target_sum = float(sum(target_shares.values()))
    if abs(target_sum - 1.0) > 1e-9:
        target_shares = {k: v / target_sum for k, v in target_shares.items()}
    populated = {
        cell: share for cell, share in target_shares.items()
        if weights[cell_ids == cell].sum() > 0
    }
    if not populated:
        return weights.copy()
    pop_sum = sum(populated.values())
    if pop_sum < 1.0 - 1e-9:
        populated = {k: v / pop_sum for k, v in populated.items()}
    new_w = weights.copy()
    for cell, share in populated.items():
        mask = cell_ids == cell
        cell_w = float(weights[mask].sum())
        target_w = total * share
        uplift = target_w / cell_w if cell_w > 0 else 1.0
        new_w[mask] = weights[mask] * uplift
    return new_w


def _max_marginal_error(
    weights: np.ndarray,
    dimensions: list[Dimension],
    cell_arrays: dict[str, np.ndarray],
    effective_targets: dict[str, dict[str, float]],
) -> tuple[float, dict[str, dict[str, float]]]:
    """Largest absolute deviation from *effective* target across all cells.

    ``effective_targets`` holds the target shares that the rake actually
    pursues (i.e. after empty-cell mass has been redistributed onto
    populated cells). Comparing against these — not the user-supplied
    raw targets — is what makes convergence well-defined.
    """
    total = float(weights.sum())
    if total == 0:
        return 0.0, {}
    final_shares: dict[str, dict[str, float]] = {}
    max_err = 0.0
    for dim in dimensions:
        cells = cell_arrays[dim.name]
        dim_targets = effective_targets[dim.name]
        dim_shares: dict[str, float] = {}
        for cell, target_share in dim_targets.items():
            mask = cells == cell
            cell_w = float(weights[mask].sum())
            obs = cell_w / total if total > 0 else 0.0
            dim_shares[cell] = obs
            err = abs(obs - target_share)
            if err > max_err:
                max_err = err
        final_shares[dim.name] = dim_shares
    return max_err, final_shares


def joint_ipf(
    units: pd.DataFrame,
    *,
    dimensions: list[Dimension],
    weight_col: str = "weight",
    max_iter: int = 50,
    tol: float = 1e-3,
) -> tuple[pd.DataFrame, JointIpfResult]:
    """Cycle through ``dimensions`` until all marginals converge.

    Parameters
    ----------
    units:
        DataFrame with a ``weight_col`` column.
    dimensions:
        List of :class:`Dimension` specs. The IPF cycles through them
        in order, raking each, until the worst marginal error is below
        ``tol`` or ``max_iter`` cycles is reached.
    max_iter:
        Maximum cycles (one cycle = one full pass over all dimensions).
        Beckman et al. (1996) recommend 20-30 for typical demographic
        rakes; we default to 50 for safety.
    tol:
        Convergence tolerance: max acceptable absolute deviation from
        any cell's target share (e.g. ``1e-3`` = 0.1pp).

    Returns
    -------
    (units_out, result):
        ``units_out`` is a copy of ``units`` with the rebalanced weight
        column. ``result`` describes convergence.
    """
    if not dimensions:
        raise ConfigError("joint_ipf requires at least one dimension")
    if weight_col not in units.columns:
        raise ConfigError(f"joint_ipf requires {weight_col!r} on units")

    df = units.copy()
    weights = df[weight_col].fillna(0).astype(float).to_numpy()
    if weights.sum() == 0:
        raise ConfigError("joint_ipf: total weight is zero")

    # Pre-compute cell arrays (once) for efficiency
    cell_arrays: dict[str, np.ndarray] = {}
    skipped_cells: dict[str, list[str]] = {}
    target_shares_resolved: dict[str, dict[str, float]] = {}
    effective_targets: dict[str, dict[str, float]] = {}
    for dim in dimensions:
        cells = np.asarray(dim.cell_fn(df))
        if len(cells) != len(df):
            raise ConfigError(
                f"dimension {dim.name!r} cell_fn returned len={len(cells)}, "
                f"expected {len(df)}"
            )
        cell_arrays[dim.name] = cells
        # Identify cells that are empty in input weights
        empty = [
            c for c in dim.target_shares
            if weights[cells == c].sum() == 0
        ]
        skipped_cells[dim.name] = empty
        target_shares_resolved[dim.name] = dict(dim.target_shares)

        # Effective targets after empty-cell redistribution. These are
        # what the rake actually pursues, and what we should validate
        # convergence against.
        populated_targets = {
            c: dim.target_shares[c]
            for c in dim.target_shares
            if c not in empty
        }
        pop_sum = sum(populated_targets.values())
        if pop_sum > 0:
            effective_targets[dim.name] = {
                c: v / pop_sum for c, v in populated_targets.items()
            }
        else:
            effective_targets[dim.name] = {}

    # IPF main loop
    iteration = 0
    max_err = float("inf")
    while iteration < max_iter and max_err > tol:
        iteration += 1
        for dim in dimensions:
            weights = _rake_to_cells(
                weights, cell_arrays[dim.name], dim.target_shares,
            )
        max_err, _ = _max_marginal_error(
            weights, dimensions, cell_arrays, effective_targets,
        )

    converged = max_err <= tol
    _, final_shares = _max_marginal_error(
        weights, dimensions, cell_arrays, effective_targets,
    )

    df[weight_col] = weights

    notes: list[str] = []
    if not converged:
        notes.append(
            f"did not converge in {max_iter} iterations (max marginal error "
            f"{max_err:.4f}, tol {tol:.4f})"
        )
    for dim_name, empty in skipped_cells.items():
        if empty:
            notes.append(
                f"{dim_name}: target cells {empty} had no observed weight; "
                "their target mass was redistributed proportionally to "
                "populated cells (total preserved)"
            )

    return df, JointIpfResult(
        n_iterations=iteration,
        converged=converged,
        max_marginal_error=max_err,
        per_dim_final_shares=final_shares,
        # Report the EFFECTIVE targets (what the rake actually pursues
        # after empty-cell redistribution) rather than the raw inputs.
        # This lets callers compare ``post_share == target_share`` directly
        # without having to redo the renormalization.
        per_dim_target_shares=effective_targets,
        per_dim_skipped_cells=skipped_cells,
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# Joint-distribution validation
# ---------------------------------------------------------------------------


@dataclass
class JointDistributionDrift:
    """Per-cell drift report comparing baseline vs post-rake joint distribution."""

    cell: tuple
    baseline_share: float
    post_share: float
    abs_drift: float

    @property
    def drift_pct(self) -> float:
        if self.baseline_share == 0:
            return 0.0
        return abs(self.post_share - self.baseline_share) / self.baseline_share


def validate_joint_distribution(
    baseline_units: pd.DataFrame,
    post_units: pd.DataFrame,
    *,
    dimensions: list[Dimension],
    weight_col: str = "weight",
    drift_threshold_rel: float = 0.10,
) -> tuple[bool, list[JointDistributionDrift]]:
    """Compare joint-cell shares before and after raking.

    Returns ``(passed, drift_list)``. ``passed`` is True when every joint
    cell's share-relative-to-baseline drift is below
    ``drift_threshold_rel`` (default 10%). The drift list is sorted
    descending by absolute drift so callers can inspect the worst cells
    first.

    Joint cells are defined by the **product** of all dimensions'
    cell labels — e.g. for ``[senior, hh_size]`` dimensions, cells are
    ``("senior", "1")``, ``("senior", "2")``, ..., ``("nonsenior", "5plus")``.
    """
    base_cells = [d.cell_fn(baseline_units) for d in dimensions]
    post_cells = [d.cell_fn(post_units) for d in dimensions]

    base_w = baseline_units[weight_col].fillna(0).astype(float).to_numpy()
    post_w = post_units[weight_col].fillna(0).astype(float).to_numpy()
    base_total = float(base_w.sum())
    post_total = float(post_w.sum())

    base_joint = pd.DataFrame({"_w": base_w})
    post_joint = pd.DataFrame({"_w": post_w})
    for i, dim in enumerate(dimensions):
        base_joint[dim.name] = base_cells[i]
        post_joint[dim.name] = post_cells[i]
    dim_names = [d.name for d in dimensions]

    base_grouped = base_joint.groupby(dim_names)["_w"].sum() / max(base_total, 1e-9)
    post_grouped = post_joint.groupby(dim_names)["_w"].sum() / max(post_total, 1e-9)

    drifts: list[JointDistributionDrift] = []
    all_cells = set(base_grouped.index) | set(post_grouped.index)
    for cell in all_cells:
        b = float(base_grouped.get(cell, 0.0))
        p = float(post_grouped.get(cell, 0.0))
        drift = JointDistributionDrift(
            cell=cell if isinstance(cell, tuple) else (cell,),
            baseline_share=b,
            post_share=p,
            abs_drift=abs(p - b),
        )
        drifts.append(drift)
    drifts.sort(key=lambda d: d.abs_drift, reverse=True)

    passed = all(d.drift_pct <= drift_threshold_rel for d in drifts)
    return passed, drifts


__all__ = [
    "Dimension",
    "JointIpfResult",
    "JointDistributionDrift",
    "joint_ipf",
    "validate_joint_distribution",
]
