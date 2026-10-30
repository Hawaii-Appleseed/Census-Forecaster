"""Geographic stratification helpers (PUMA, county, district).

Phase 8 adds convenience wrappers around the metrics primitives so
results can be reported at sub-state granularity without manually
groupby-ing every time.

The :class:`tax_modeler.revenue.RevenueEstimator` already exposes a
``by_district(col)`` method for tax-unit-level aggregation; this module
covers the *poverty* and *SPM* side, which operates on resource and
threshold columns rather than tax dollars.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError, DataValidationError

from .distribution import gini, palma
from .poverty import poverty_depth, poverty_gap, poverty_rate


def poverty_by_geography(
    units: pd.DataFrame,
    *,
    resources_col: str,
    threshold,
    weight_col: str = "weight",
    geo_col: str = "PUMA",
    person_weighted: bool = True,
    persons_per_unit_col: str | None = None,
) -> pd.DataFrame:
    """Compute SPM-style poverty metrics broken down by a geographic column.

    Parameters
    ----------
    units:
        DataFrame with at least ``resources_col``, ``weight_col``, and
        ``geo_col``.
    resources_col:
        Column holding per-unit SPM resources (or any other resource
        concept).
    threshold:
        Either a scalar (single statewide threshold) or an
        equal-length array/Series providing a per-unit threshold (e.g.
        from :func:`tax_modeler.poverty.threshold_for_units`).
    geo_col:
        Geographic identifier to group on. Defaults to ``"PUMA"``;
        ``"county"`` and ``"house_district"`` are also valid if those
        columns exist on the units frame.
    person_weighted:
        When ``True`` (default), the *headcount* metrics (``poverty_rate``
        and ``poverty_depth``) are weighted by **persons** — i.e. the
        share of *people* below the threshold — matching the canonical SPM
        path in :func:`tax_modeler.poverty.impact`. Person weight is
        ``weight_col * persons_per_unit``. Dollar metrics (``poverty_gap_$``,
        ``mean_resources_$``) always stay on the resource-unit weight.
        Set ``False`` to recover the legacy unit-weighted rate.
    persons_per_unit_col:
        Optional column giving persons per resource unit. If omitted, the
        count is auto-derived: the ``n_persons`` column (SPM-unit frames)
        or ``1 + (filing_status == "married_filing_jointly") + num_dependents``
        (tax-unit frames), via the shared
        :func:`tax_modeler.poverty.impact._persons_per_unit` helper.

    Returns
    -------
    pd.DataFrame
        One row per geography with: ``count_weighted`` (resource units),
        ``count_weighted_persons``, ``poverty_rate``, ``poverty_depth``,
        ``poverty_gap_$`` (within that geography), ``mean_resources_$``.
    """
    for c in (resources_col, weight_col, geo_col):
        if c not in units.columns:
            raise DataValidationError(
                f"poverty_by_geography requires column {c!r}", missing_columns=[c]
            )

    if isinstance(threshold, (pd.Series, np.ndarray, list, tuple)):
        thr_arr = np.asarray(threshold, dtype=float)
        if len(thr_arr) != len(units):
            raise DataValidationError(
                f"threshold length {len(thr_arr)} != len(units) {len(units)}"
            )
    else:
        thr_arr = np.full(len(units), float(threshold))

    # Persons-per-unit for person-weighted headcount rates. Derived once
    # over the full frame so group subsets stay aligned by position.
    if person_weighted:
        if persons_per_unit_col is not None:
            if persons_per_unit_col not in units.columns:
                raise DataValidationError(
                    f"poverty_by_geography requires column {persons_per_unit_col!r}",
                    missing_columns=[persons_per_unit_col],
                )
            ppu_arr = (
                units[persons_per_unit_col].fillna(0).clip(lower=0).to_numpy(dtype=float)
            )
        else:
            # Canonical auto-detection (SPM-unit n_persons or tax-unit
            # composition). Imported locally to avoid a metrics<->poverty
            # import cycle at module load.
            from tax_modeler.poverty.impact import _persons_per_unit

            ppu_arr = _persons_per_unit(units)
    else:
        ppu_arr = np.ones(len(units), dtype=float)

    rows = []
    for geo, group in units.groupby(geo_col, observed=True):
        idx = group.index
        pos = units.index.get_indexer(idx)
        thr_subset = thr_arr[pos]
        r = group[resources_col].fillna(0).to_numpy(dtype=float)
        w = group[weight_col].fillna(0).to_numpy(dtype=float)
        if w.sum() == 0:
            continue
        # Person weight for headcount metrics; unit weight for $ metrics.
        wp = w * ppu_arr[pos]
        rate_w = wp if wp.sum() > 0 else w
        rows.append({
            geo_col: geo,
            "count_weighted": float(w.sum()),
            "count_weighted_persons": float(wp.sum()),
            "poverty_rate": poverty_rate(r, thr_subset, rate_w),
            "poverty_depth": poverty_depth(r, thr_subset, rate_w),
            "poverty_gap_$": poverty_gap(r, thr_subset, w),
            "mean_resources_$": float((r * w).sum() / w.sum()),
        })
    return pd.DataFrame(rows).set_index(geo_col).sort_index()


def distribution_by_geography(
    units: pd.DataFrame,
    *,
    value_col: str,
    weight_col: str = "weight",
    geo_col: str = "PUMA",
    indices: Sequence[str] = ("gini", "palma"),
) -> pd.DataFrame:
    """Per-geography inequality indices on a value column.

    Parameters
    ----------
    units, value_col, weight_col, geo_col:
        Standard DataFrame inputs.
    indices:
        Subset of ``("gini", "palma")``. Atkinson is omitted because its
        ``eps`` parameter complicates the API; call atkinson directly
        per group if needed.
    """
    for c in (value_col, weight_col, geo_col):
        if c not in units.columns:
            raise DataValidationError(
                f"distribution_by_geography requires column {c!r}",
                missing_columns=[c],
            )
    bad = set(indices) - {"gini", "palma"}
    if bad:
        raise ConfigError(
            f"unknown inequality indices: {sorted(bad)}",
            available=["gini", "palma"],
        )

    rows = []
    for geo, group in units.groupby(geo_col, observed=True):
        v = group[value_col].fillna(0).to_numpy(dtype=float)
        w = group[weight_col].fillna(0).to_numpy(dtype=float)
        if w.sum() == 0 or len(v) < 2:
            continue
        # Inequality indices need non-negative values
        v_pos = np.clip(v, 0.0, None)
        row = {
            geo_col: geo,
            "count_weighted": float(w.sum()),
            "mean_$": float((v * w).sum() / w.sum()),
        }
        if "gini" in indices:
            row["gini"] = gini(v_pos, w)
        if "palma" in indices:
            row["palma"] = palma(v_pos, w)
        rows.append(row)
    return pd.DataFrame(rows).set_index(geo_col).sort_index()


__all__ = ["poverty_by_geography", "distribution_by_geography"]
