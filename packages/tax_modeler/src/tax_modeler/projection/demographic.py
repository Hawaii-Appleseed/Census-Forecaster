"""Demographic projection for ACS-derived microsim populations.

The income-side ``project_tax_units_forward`` ages incomes to a target
year via wage-growth + bracket-migration. It does **not** age the
*demographic* composition — a 2030 projected frame still has 2023's age
structure, household-size distribution, and disability prevalence.

For policy-impact analysis that span 5+ years (especially benefits like
SSI/Medicaid that key on age) this matters: Hawaii's 65+ share is rising
from ~19% (2020) toward ~28% (2050) per DBEDT Series 2050 projections.

This module provides simple post-stratification raking on one or more
demographic dimensions to align the projected frame with target marginals.

Phase 8 ships **age-only** raking (the highest-leverage dimension for
benefit modeling). Multi-dimensional (age × household size × disability)
raking is a straightforward extension via the existing IPF orchestrator
in ``tax_modeler.calibration.ipf_calibration``.

Sources for the embedded HI senior-share trajectory:
  * DBEDT, "Population and Economic Projections for the State of Hawaii
    to 2050" (April 2024 release)
  * Cross-checked against Census ACS 5-year for 2020 baseline
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError

# ---------------------------------------------------------------------------
# DBEDT Series 2050 senior-share trajectory (HI residents 65+)
# ---------------------------------------------------------------------------

_HI_SENIOR_SHARE = {
    2020: 0.190,
    2025: 0.215,
    2030: 0.235,
    2035: 0.250,
    2040: 0.265,
    2045: 0.275,
    2050: 0.280,
}

# ---------------------------------------------------------------------------
# Hawaii household-size distribution trajectory
# ---------------------------------------------------------------------------
#
# Source: Census ACS 5-year HI HH-size tables (2019-2024 baseline) plus
# DBEDT projection of mean HH size declining toward 2.6 by 2050. We model
# the shift as a small-but-steady reduction in the share of large
# households (5+) in favor of singletons and 2-person households. Bins:
#   1 person, 2 persons, 3 persons, 4 persons, 5+ persons.

_HI_HH_SIZE_DIST = {
    # year -> (share_1, share_2, share_3, share_4, share_5plus)
    2020: (0.260, 0.310, 0.165, 0.135, 0.130),
    2030: (0.275, 0.318, 0.165, 0.130, 0.112),
    2040: (0.290, 0.325, 0.165, 0.125, 0.095),
    2050: (0.300, 0.330, 0.165, 0.120, 0.085),
}

# ---------------------------------------------------------------------------
# Hawaii disability prevalence
# ---------------------------------------------------------------------------
# Source: ACS 5-year HI disability rate, 2019-2024 average. HHS DAS
# Hawaii projection is approximately flat (slight increase with
# population aging is captured indirectly via the senior-share rake).
_HI_DISABILITY_SHARE_BASE = 0.135
_HI_DISABILITY_SHARE = {  # included as a dict for forward-compatibility
    2020: 0.130,
    2030: 0.135,
    2040: 0.140,
    2050: 0.143,
}


def _interp_year(table: dict, year: int) -> float | tuple:
    """Generic linear interpolation across a year-keyed lookup."""
    years_sorted = sorted(table)
    if year <= years_sorted[0]:
        return table[years_sorted[0]]
    if year >= years_sorted[-1]:
        return table[years_sorted[-1]]
    for lo, hi in zip(years_sorted, years_sorted[1:]):
        if lo <= year <= hi:
            t = (year - lo) / (hi - lo)
            v_lo = table[lo]
            v_hi = table[hi]
            if isinstance(v_lo, tuple):
                return tuple(a + t * (b - a) for a, b in zip(v_lo, v_hi))
            return v_lo + t * (v_hi - v_lo)
    raise ConfigError(f"_interp_year: {year} not interpolable")


def hawaii_hh_size_dist(year: int) -> tuple[float, float, float, float, float]:
    """Return projected HH-size shares (1, 2, 3, 4, 5+) for the given year."""
    return tuple(_interp_year(_HI_HH_SIZE_DIST, year))  # type: ignore[arg-type]


def hawaii_disability_share(year: int) -> float:
    """Return projected HI disability prevalence for the given year."""
    return float(_interp_year(_HI_DISABILITY_SHARE, year))


def hawaii_demographic_targets(year: int) -> dict:
    """Bundle the three Phase 9b demographic-projection targets for ``year``.

    Returns a dict suitable for passing as ``targets`` to
    :func:`project_demographics_forward` with ``dimensions=["senior",
    "hh_size", "disability"]``.
    """
    return {
        "senior": hawaii_senior_share(year),
        "hh_size": hawaii_hh_size_dist(year),
        "disability": hawaii_disability_share(year),
    }


def hawaii_senior_share(year: int) -> float:
    """Linearly interpolate HI 65+ population share for a given year."""
    if year <= min(_HI_SENIOR_SHARE):
        return _HI_SENIOR_SHARE[min(_HI_SENIOR_SHARE)]
    if year >= max(_HI_SENIOR_SHARE):
        return _HI_SENIOR_SHARE[max(_HI_SENIOR_SHARE)]
    years_sorted = sorted(_HI_SENIOR_SHARE)
    for lo, hi in zip(years_sorted, years_sorted[1:]):
        if lo <= year <= hi:
            t = (year - lo) / (hi - lo)
            return _HI_SENIOR_SHARE[lo] + t * (_HI_SENIOR_SHARE[hi] - _HI_SENIOR_SHARE[lo])
    raise ConfigError(f"hawaii_senior_share: year={year} not interpolable")


# ---------------------------------------------------------------------------
# Demographic projection
# ---------------------------------------------------------------------------


@dataclass
class DemographicProjectionResult:
    """Output of :func:`project_demographics_forward`.

    The single-dim path (back-compat) populates ``senior_share_*``,
    ``weight_uplift_seniors``, and ``weight_uplift_non_seniors``. The
    multi-dim path (Phase 9b) additionally populates ``per_dim_results``
    with one entry per dimension raked.
    """

    target_year: int
    senior_share_baseline: float
    senior_share_target: float
    weight_uplift_seniors: float          # multiplier applied to senior weights
    weight_uplift_non_seniors: float      # multiplier applied to non-senior weights
    notes: tuple[str, ...] = ()
    # Multi-dim outputs (Phase 9b). Each entry: dimension -> dict with keys
    #   "baseline_shares", "target_shares", "post_shares", "uplifts"
    # for cells that ran. Populated only when dimensions != ["senior"].
    per_dim_results: dict = field(default_factory=dict)


_VALID_DIMENSIONS = ("senior", "hh_size", "disability")


def _build_dimension_specs(
    dimensions: list,
    targets: dict,
    age_threshold: int,
    age_col: str,
):
    """Construct ``Dimension`` objects (for joint_ipf) from string dim names."""
    from tax_modeler.calibration.joint_ipf import Dimension

    specs = []
    for dim in dimensions:
        if dim == "senior":
            target = float(targets["senior"])
            target_shares = {"senior": target, "nonsenior": 1.0 - target}

            def _cells(df, _age_col=age_col, _at=age_threshold):
                age = df[_age_col].fillna(0).astype(float).to_numpy()
                return np.where(age >= _at, "senior", "nonsenior")

            specs.append(Dimension(
                name="senior", cell_fn=_cells, target_shares=target_shares,
            ))
        elif dim == "hh_size":
            target = targets["hh_size"]
            target_shares = {
                "1": float(target[0]), "2": float(target[1]),
                "3": float(target[2]), "4": float(target[3]),
                "5plus": float(target[4]),
            }

            def _cells(df):
                is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
                n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
                hh_size = 1 + is_joint.astype(int) + n_dep
                return np.where(hh_size >= 5, "5plus", hh_size.astype(str))

            specs.append(Dimension(
                name="hh_size", cell_fn=_cells, target_shares=target_shares,
            ))
        elif dim == "disability":
            target = float(targets["disability"])
            target_shares = {
                "disabled": target, "nondisabled": 1.0 - target,
            }

            def _cells(df):
                if "n_disabled" not in df.columns:
                    return np.array(["nondisabled"] * len(df))
                n_dis = df["n_disabled"].fillna(0).astype(int).to_numpy()
                return np.where(n_dis > 0, "disabled", "nondisabled")

            specs.append(Dimension(
                name="disability", cell_fn=_cells, target_shares=target_shares,
            ))
        else:
            raise ConfigError(f"unknown dimension: {dim!r}",
                              available=list(_VALID_DIMENSIONS))
    return specs


def _project_via_joint_ipf(
    units: pd.DataFrame,
    *,
    target_year: int,
    dimensions: list,
    targets: dict,
    age_threshold: int,
    age_col: str,
    weight_col: str,
    max_iter: int,
    tol: float,
) -> tuple[pd.DataFrame, DemographicProjectionResult]:
    """Phase 10: route demographic projection through the joint IPF engine."""
    from tax_modeler.calibration.joint_ipf import joint_ipf

    specs = _build_dimension_specs(dimensions, targets, age_threshold, age_col)
    out_df, ipf_result = joint_ipf(
        units, dimensions=specs, weight_col=weight_col,
        max_iter=max_iter, tol=tol,
    )

    # Translate to the legacy DemographicProjectionResult shape so callers
    # (and existing smoke tests) don't need to change.
    per_dim: dict = {}
    for dim_name in [d.name for d in specs]:
        target_shares = ipf_result.per_dim_target_shares.get(dim_name, {})
        post_shares = ipf_result.per_dim_final_shares.get(dim_name, {})
        skipped = ipf_result.per_dim_skipped_cells.get(dim_name, [])
        # Compute baseline shares from the input (one rake of zero cycles)
        baseline_shares: dict = {}
        for cell in target_shares:
            mask = specs[[s.name for s in specs].index(dim_name)].cell_fn(units) == cell
            cell_w = float(units[weight_col].fillna(0).astype(float).to_numpy()[mask].sum())
            total = float(units[weight_col].fillna(0).astype(float).to_numpy().sum())
            baseline_shares[cell] = cell_w / total if total > 0 else 0.0
        per_dim[dim_name] = {
            "baseline_shares": baseline_shares,
            "target_shares": target_shares,
            "post_shares": post_shares,
            "uplifts": {},  # IPF doesn't expose per-cell uplifts — multi-step
            "skipped_empty_cells": skipped,
        }

    senior_info = per_dim.get("senior", {})
    senior_baseline = senior_info.get("baseline_shares", {}).get("senior", 0.0)
    senior_target_val = (
        targets.get("senior")
        if "senior" in dimensions
        else hawaii_senior_share(target_year)
    )

    notes = list(ipf_result.notes)
    notes.append(
        f"joint_ipf converged={ipf_result.converged} after "
        f"{ipf_result.n_iterations} iterations "
        f"(max marginal error {ipf_result.max_marginal_error:.4f})"
    )

    return out_df, DemographicProjectionResult(
        target_year=target_year,
        senior_share_baseline=senior_baseline,
        senior_share_target=senior_target_val,
        weight_uplift_seniors=1.0,    # IPF uses iterated multi-step uplifts
        weight_uplift_non_seniors=1.0,
        notes=tuple(notes),
        per_dim_results=per_dim,
    )


def _rake_to_cells(
    weights: np.ndarray,
    cell_ids: np.ndarray,
    target_shares: dict,
) -> tuple[np.ndarray, dict]:
    """Two-cell-or-more post-stratification rake on a single dimension.

    ``cell_ids`` must be the same length as ``weights`` and provide a
    cell label per row. ``target_shares`` maps cell label -> desired
    share of total weight (must sum to 1.0).

    Returns ``(new_weights, info)`` where ``info`` includes per-cell
    baseline shares, target shares, post-rake shares, and the multiplier
    applied. Total weight is preserved exactly.
    """
    total = float(weights.sum())
    if total == 0:
        return weights.copy(), {"baseline_shares": {}, "post_shares": {}}
    target_sum = float(sum(target_shares.values()))
    if abs(target_sum - 1.0) > 1e-6:
        # Renormalize to be safe
        target_shares = {k: v / target_sum for k, v in target_shares.items()}
    # Drop cells that have no observed weight, then renormalize remaining
    # targets to sum to 1.0. Without this, target mass allocated to empty
    # cells "vanishes" and total weight isn't preserved.
    populated = {
        cell: share for cell, share in target_shares.items()
        if weights[cell_ids == cell].sum() > 0
    }
    if not populated:
        return weights.copy(), {
            "baseline_shares": {},
            "target_shares": dict(target_shares),
            "post_shares": {},
            "uplifts": {},
            "skipped": "no populated cells in input",
        }
    pop_sum = sum(populated.values())
    if pop_sum < 1.0 - 1e-9:
        # Some target cells were empty — redistribute their share proportionally
        # over the populated cells so totals are preserved.
        populated = {k: v / pop_sum for k, v in populated.items()}
    baseline_shares: dict = {}
    uplifts: dict = {}
    new_w = weights.copy()
    for cell, share in populated.items():
        mask = cell_ids == cell
        cell_w = float(weights[mask].sum())
        baseline_shares[cell] = cell_w / total if total > 0 else 0.0
        target_w = total * share
        uplift = target_w / cell_w if cell_w > 0 else 1.0
        uplifts[cell] = uplift
        new_w[mask] = weights[mask] * uplift
    # Report post_shares only for the cells we actually raked (populated).
    # Empty target cells retain a zero post_share but are reported separately
    # so callers can distinguish "achieved" from "skipped because empty".
    post_shares = {
        cell: float(new_w[cell_ids == cell].sum() / new_w.sum())
        for cell in populated
    }
    skipped_cells = sorted(set(target_shares) - set(populated))
    info = {
        "baseline_shares": baseline_shares,
        "target_shares": dict(populated),     # what we actually targeted
        "original_target_shares": dict(target_shares),  # what was requested
        "post_shares": post_shares,
        "uplifts": uplifts,
    }
    if skipped_cells:
        info["skipped_empty_cells"] = skipped_cells
    return new_w, info


def _apply_senior_rake(
    df: pd.DataFrame, *, age_col: str, weight_col: str,
    age_threshold: int, target: float,
) -> tuple[pd.DataFrame, dict]:
    age = df[age_col].fillna(0).astype(float).to_numpy()
    cells = np.where(age >= age_threshold, "senior", "nonsenior")
    new_w, info = _rake_to_cells(
        df[weight_col].fillna(0).astype(float).to_numpy(), cells,
        {"senior": target, "nonsenior": 1.0 - target},
    )
    df = df.copy()
    df[weight_col] = new_w
    return df, info


def _apply_hh_size_rake(
    df: pd.DataFrame, *, weight_col: str, target: tuple,
) -> tuple[pd.DataFrame, dict]:
    """Rake on household size bins (1, 2, 3, 4, 5+)."""
    is_joint = (df["filing_status"] == "married_filing_jointly").to_numpy()
    n_dep = df["num_dependents"].fillna(0).astype(int).to_numpy()
    hh_size = 1 + is_joint.astype(int) + n_dep
    cells = np.where(hh_size >= 5, "5plus", hh_size.astype(str))
    target_dict = {
        "1": float(target[0]),
        "2": float(target[1]),
        "3": float(target[2]),
        "4": float(target[3]),
        "5plus": float(target[4]),
    }
    new_w, info = _rake_to_cells(
        df[weight_col].fillna(0).astype(float).to_numpy(), cells, target_dict,
    )
    df = df.copy()
    df[weight_col] = new_w
    return df, info


def _apply_disability_rake(
    df: pd.DataFrame, *, weight_col: str, target: float,
) -> tuple[pd.DataFrame, dict]:
    if "n_disabled" not in df.columns:
        return df, {
            "baseline_shares": {},
            "target_shares": {"disabled": target, "nondisabled": 1.0 - target},
            "post_shares": {},
            "uplifts": {},
            "skipped": "no n_disabled column on units; run enrich_for_benefits first",
        }
    n_dis = df["n_disabled"].fillna(0).astype(int).to_numpy()
    cells = np.where(n_dis > 0, "disabled", "nondisabled")
    new_w, info = _rake_to_cells(
        df[weight_col].fillna(0).astype(float).to_numpy(), cells,
        {"disabled": target, "nondisabled": 1.0 - target},
    )
    df = df.copy()
    df[weight_col] = new_w
    return df, info


def project_demographics_forward(
    units: pd.DataFrame,
    *,
    target_year: int,
    base_year: int = 2024,
    age_threshold: int = 65,
    age_col: str = "primary_agep",
    weight_col: str = "weight",
    senior_share_target: float | None = None,
    dimensions: list | None = None,
    targets: dict | None = None,
    use_joint_ipf: bool = False,
    joint_ipf_max_iter: int = 50,
    joint_ipf_tol: float = 1e-3,
) -> tuple[pd.DataFrame, DemographicProjectionResult]:
    """Reweight ``units`` so the implied senior share matches ``target_year``.

    Uses two-cell post-stratification (seniors vs non-seniors). Weights
    on units where ``primary_agep >= age_threshold`` are scaled up so
    that the population-weighted senior share matches the DBEDT-projected
    target for ``target_year``. Non-senior weights are scaled down to
    preserve the total population.

    Returns a copy of ``units`` with adjusted ``weight_col`` plus a
    :class:`DemographicProjectionResult` describing the adjustment.

    Notes
    -----
    * The aggregate population (sum of weights) is preserved.
    * Anyone whose primary_age is missing is treated as non-senior.
    * For backwards-compatible behavior, callers can recover the
      original weight column from ``units[weight_col]`` *before* this
      function is applied.
    * This is **post-stratification raking on one dimension**, not a
      full multi-dimensional IPF. For multi-cell rakes (age × HH size,
      etc.), use :class:`tax_modeler.calibration.IPFCalibrator` directly.
    """
    if age_col not in units.columns:
        raise ConfigError(
            f"project_demographics_forward requires {age_col!r} on units"
        )
    if weight_col not in units.columns:
        raise ConfigError(
            f"project_demographics_forward requires {weight_col!r} on units"
        )

    # Multi-dim path: dimensions + targets are explicit kwargs. The
    # default (None) preserves the prior single-dim "senior only" behavior.
    if dimensions is not None and dimensions != ["senior"]:
        bad = [d for d in dimensions if d not in _VALID_DIMENSIONS]
        if bad:
            raise ConfigError(
                f"unknown projection dimensions: {bad}",
                available=list(_VALID_DIMENSIONS),
            )
        if targets is None:
            targets = hawaii_demographic_targets(target_year)

        # Phase 10: use_joint_ipf=True routes through the proper joint IPF
        # which iterates until ALL marginals converge simultaneously rather
        # than running independent rakes that can interfere.
        if use_joint_ipf:
            return _project_via_joint_ipf(
                units, target_year=target_year, dimensions=dimensions,
                targets=targets, age_threshold=age_threshold,
                age_col=age_col, weight_col=weight_col,
                max_iter=joint_ipf_max_iter, tol=joint_ipf_tol,
            )

        df = units.copy()
        per_dim: dict = {}
        notes: list[str] = []
        # Apply rakes sequentially. Note: independent rakes on different
        # dimensions can degrade each other's marginals slightly because
        # of correlations (a younger HH-size distribution has its own
        # senior-share implication). Per the user's scope decision,
        # we accept this as a documented limitation; promote to true
        # joint IPF in a future phase if needed.
        for dim in dimensions:
            if dim == "senior":
                df, info = _apply_senior_rake(
                    df, age_col=age_col, weight_col=weight_col,
                    age_threshold=age_threshold, target=targets["senior"],
                )
            elif dim == "hh_size":
                df, info = _apply_hh_size_rake(
                    df, weight_col=weight_col, target=targets["hh_size"],
                )
            elif dim == "disability":
                df, info = _apply_disability_rake(
                    df, weight_col=weight_col, target=targets["disability"],
                )
            else:
                continue
            per_dim[dim] = info
            # Sanity warning if any rake's baseline share is far from target
            for cell, target_share in info.get("target_shares", {}).items():
                baseline = info.get("baseline_shares", {}).get(cell, target_share)
                if abs(baseline - target_share) > 0.10:
                    notes.append(
                        f"{dim}/{cell}: baseline {baseline:.3f} → target "
                        f"{target_share:.3f} (>10pp shift; sanity check)"
                    )
        # Populate the back-compat scalar fields from the senior result if present
        senior_info = per_dim.get("senior", {})
        senior_baseline = senior_info.get("baseline_shares", {}).get("senior", 0.0)
        senior_target_val = (
            targets.get("senior")
            if "senior" in dimensions and senior_info
            else hawaii_senior_share(target_year)
        )
        senior_uplift = senior_info.get("uplifts", {}).get("senior", 1.0)
        nonsenior_uplift = senior_info.get("uplifts", {}).get("nonsenior", 1.0)
        return df, DemographicProjectionResult(
            target_year=target_year,
            senior_share_baseline=senior_baseline,
            senior_share_target=senior_target_val,
            weight_uplift_seniors=senior_uplift,
            weight_uplift_non_seniors=nonsenior_uplift,
            notes=tuple(notes),
            per_dim_results=per_dim,
        )

    # Single-dim back-compat path (senior-only post-stratification).
    df = units.copy()
    if senior_share_target is None:
        senior_share_target = hawaii_senior_share(target_year)
    senior_share_baseline_in = hawaii_senior_share(base_year)

    is_senior = df[age_col].fillna(0).astype(float).to_numpy() >= age_threshold
    w = df[weight_col].fillna(0).astype(float).to_numpy()
    total = float(w.sum())
    senior_w = float(w[is_senior].sum())
    nonsenior_w = float(w[~is_senior].sum())
    if total == 0:
        raise ConfigError("project_demographics_forward: total weight is zero")

    senior_share_obs = senior_w / total

    # Target: senior share = senior_share_target while preserving total
    target_senior_w = total * senior_share_target
    target_nonsenior_w = total * (1.0 - senior_share_target)

    # Avoid division by zero
    senior_uplift = (
        target_senior_w / senior_w if senior_w > 0 else 1.0
    )
    nonsenior_uplift = (
        target_nonsenior_w / nonsenior_w if nonsenior_w > 0 else 1.0
    )
    new_w = np.where(is_senior, w * senior_uplift, w * nonsenior_uplift)
    df[weight_col] = new_w

    notes = []
    if senior_share_obs == 0:
        notes.append(
            "no senior units in baseline frame; senior weights remain zero "
            "(consider adding a synthetic senior unit before projection)"
        )
    if senior_share_target < senior_share_obs:
        notes.append(
            "target senior share is below baseline observed share; "
            "this scales seniors *down* (e.g. modeling a hypothetical "
            "younger Hawaii)"
        )

    result = DemographicProjectionResult(
        target_year=target_year,
        senior_share_baseline=senior_share_obs,
        senior_share_target=senior_share_target,
        weight_uplift_seniors=senior_uplift,
        weight_uplift_non_seniors=nonsenior_uplift,
        notes=tuple(notes),
    )
    return df, result


__all__ = [
    "DemographicProjectionResult",
    "hawaii_senior_share",
    "hawaii_hh_size_dist",
    "hawaii_disability_share",
    "hawaii_demographic_targets",
    "project_demographics_forward",
]
