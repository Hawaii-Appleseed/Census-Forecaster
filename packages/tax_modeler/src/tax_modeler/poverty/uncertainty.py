"""SDR sampling-variance for the SPM poverty pipeline.

This is the thin poverty-specific glue over the generic
:mod:`tax_modeler.uncertainty` SDR engine. The expensive work — building
SPM units, computing resources under baseline and every counterfactual —
is done once by :func:`tax_modeler.poverty.impact.compute_poverty_impact`.
Its returned ``result.units`` frame carries, per SPM unit, the
full-sample weight (``weight``), the baseline + per-scenario SPM
resources, the SPM threshold, ``n_persons``, and — when the report is run
with ``--replicate-se`` — the 80 ACS household replicate weights
(``WGTP1`` .. ``WGTP80``).

Because replicate weights change only the *aggregation*, never the
per-unit poverty status, each published poverty statistic is just a
weighted re-aggregation of the same per-unit vectors under each of the
81 weight columns. Feeding those 81 re-aggregations to the SDR estimator
yields the ACS sampling standard error and 90% confidence interval at no
extra tax-engine cost.

The point estimates emitted here (column 0 of every weight matrix is the
full-sample weight) reproduce
:func:`tax_modeler.poverty.impact._aggregate` exactly, so the ``*_se`` /
``*_ci90_*`` columns can be joined straight onto the point-estimate
tables.

**Scope.** SDR captures ACS *sampling* variance only — NOT model
uncertainty (imputation, benefit take-up, CBO aging, calibration
targets). Emitted intervals must be labelled accordingly.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from tax_modeler.errors import DataValidationError
from tax_modeler.uncertainty import Z_90, estimate_ratio, estimate_total

from .impact import _DEFAULT_SCENARIOS, _REMOVAL_LIKE_SCENARIOS, _persons_per_unit


def _is_removal_like(scenario: str) -> bool:
    """Sign convention mirror of :func:`impact._aggregate` for a scenario."""
    return scenario.startswith("no_") or scenario in _REMOVAL_LIKE_SCENARIOS


def compute_poverty_sdr(
    units: pd.DataFrame,
    *,
    replicate_weight_cols: Sequence[str],
    scenarios: Sequence[str] = _DEFAULT_SCENARIOS,
    weight_col: str = "weight",
    group_col: str | None = None,
    z: float = Z_90,
) -> pd.DataFrame:
    """SDR sampling SE + 90% CI for the headline poverty statistics.

    Parameters
    ----------
    units:
        The ``result.units`` frame from
        :func:`tax_modeler.poverty.impact.compute_poverty_impact`, which
        must carry ``spm_resources``, ``spm_threshold``,
        ``spm_resources_{scn}`` for each scenario, ``n_persons``,
        ``weight_col``, and every column in ``replicate_weight_cols``.
    replicate_weight_cols:
        The ACS household replicate-weight columns (``WGTP1`` ..
        ``WGTP80``). Combined with ``weight_col`` (column 0) they form the
        ``(n_units, 1 + R)`` weight matrix the SDR engine re-aggregates.
    scenarios:
        Which counterfactuals to emit ``persons_lifted_{scn}`` /
        ``gap_closed_{scn}_$`` intervals for.
    group_col:
        ``None`` for a single statewide row, or a column name (e.g.
        ``"county"``) for one row per group.
    z:
        Normal critical value for the CI half-width (default 1.645 → 90%).

    Returns
    -------
    DataFrame with one row per group. For each statistic it carries the
    point estimate plus ``_se`` / ``_ci90_low`` / ``_ci90_high`` columns:
    ``poverty_rate_baseline``, ``persons_in_poverty_baseline``,
    ``poverty_gap_baseline_$``, and per scenario
    ``persons_lifted_{scn}`` / ``gap_closed_{scn}_$``.
    """
    rep_cols = list(replicate_weight_cols)
    if not rep_cols:
        raise DataValidationError(
            "compute_poverty_sdr: replicate_weight_cols is empty; nothing to "
            "vary. Pass WGTP1..WGTP80."
        )
    required = {weight_col, "spm_resources", "spm_threshold", *rep_cols}
    required |= {f"spm_resources_{scn}" for scn in scenarios}
    if group_col is not None:
        required.add(group_col)
    missing = required - set(units.columns)
    if missing:
        raise DataValidationError(
            f"compute_poverty_sdr missing required columns: {sorted(missing)}. "
            "Run compute_poverty_impact on a frame plumbed with replicate "
            "weights (poverty_impact_report.py --replicate-se)."
        )

    # ---- Per-unit vectors (computed once over the full frame) ----------
    npp = _persons_per_unit(units)
    thr = units["spm_threshold"].to_numpy(dtype=float)
    base_r = units["spm_resources"].to_numpy(dtype=float)
    poor_base = (base_r < thr).astype(float)
    gap_base = np.maximum(thr - base_r, 0.0)

    scn_poor: dict[str, np.ndarray] = {}
    scn_gap: dict[str, np.ndarray] = {}
    for scn in scenarios:
        scn_r = units[f"spm_resources_{scn}"].to_numpy(dtype=float)
        scn_poor[scn] = (scn_r < thr).astype(float)
        scn_gap[scn] = np.maximum(thr - scn_r, 0.0)

    # ---- Full (n_units, 1 + R) weight matrix; column 0 = full sample ---
    w_full = units[[weight_col, *rep_cols]].to_numpy(dtype=float)

    # ---- Groups (positional indices, mirroring impact._aggregate) ------
    if group_col is None:
        groups: list[tuple[object, np.ndarray]] = [
            ("__state__", np.arange(len(units)))
        ]
    else:
        groups = [
            (key, np.asarray(pos, dtype=int))
            for key, pos in units.groupby(
                group_col, observed=True, dropna=False
            ).indices.items()
        ]

    rows: list[dict[str, object]] = []
    for key, pos in groups:
        w = w_full[pos]
        npp_g = npp[pos]
        poor_base_g = poor_base[pos]
        person_base = poor_base_g * npp_g

        row: dict[str, object] = {}
        if group_col is not None:
            row[group_col] = key

        # Baseline rate is a ratio estimator (numerator/denominator share
        # the same weight column, so the SDR captures their covariance).
        row.update(
            estimate_ratio(person_base, npp_g, w, z=z).as_dict("poverty_rate_baseline")
        )
        row.update(
            estimate_total(person_base, w, z=z).as_dict("persons_in_poverty_baseline")
        )
        row.update(
            estimate_total(gap_base[pos], w, z=z).as_dict("poverty_gap_baseline_$")
        )

        for scn in scenarios:
            poor_scn_g = scn_poor[scn][pos]
            if _is_removal_like(scn):
                lift_vals = (poor_scn_g - poor_base_g) * npp_g
                gap_vals = scn_gap[scn][pos] - gap_base[pos]
            else:
                lift_vals = (poor_base_g - poor_scn_g) * npp_g
                gap_vals = gap_base[pos] - scn_gap[scn][pos]
            row.update(
                estimate_total(lift_vals, w, z=z).as_dict(f"persons_lifted_{scn}")
            )
            row.update(
                estimate_total(gap_vals, w, z=z).as_dict(f"gap_closed_{scn}_$")
            )

        rows.append(row)

    out = pd.DataFrame(rows)
    if group_col is not None and not out.empty:
        out = out.sort_values(group_col).reset_index(drop=True)
    return out


__all__ = ["compute_poverty_sdr"]
