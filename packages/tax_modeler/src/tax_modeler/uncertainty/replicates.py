"""Calibrated replicate weights for the tax-unit frames: "rake once, score replicates".

The tax-unit cache carries the 80 PUMS replicate weights as
``weight_r01..weight_r80`` (``units.constructor``), built with the same
hybrid rule and filing-status factors as ``weight``. Everything the revenue
path then does to ``weight`` is multiplicative per unit: the IPF rake
(``pipeline.calibrate``), zeroing the $1M+ survey records at synthesis, the
migration response. None of it is re-run per replicate. Instead the
**calibration ratio** ``weight / weight_uncal`` — the unit's current weight
over its weight when the cache was loaded (:func:`snapshot_uncalibrated_weight`)
— is applied to each replicate::

    w_r(unit) = weight_r(unit) x weight(unit) / weight_uncal(unit)

so a replicate moves with the rake and the response exactly as the main
weight did. This is the first-order shortcut FORECAST_ASSESSMENT_2026-06-11.md
§9 accepts: the rake's own sampling variability (rake-per-replicate) is not
in the band.

Rows the PUMS did not sample get no replicate variation: the synthetic $1M+
tail (``is_synthetic_ultra_high``) is a Pareto draw anchored on DOTAX's
administrative counts, so every replicate column carries its ``weight``. A
row with no usable replicate (missing column value, or no uncalibrated
weight) does the same.

The band this produces is **ACS sampling variance only**. It does not cover
the DOTAX anchors, the aging, the behavioral parameters (those are the
LOW/MID/HIGH scenarios) or the credit overlay, which is an aggregate model
with no microdata behind it.
"""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np
import pandas as pd

from tax_modeler.uncertainty.sdr import ACS_REPLICATES, SDREstimate, Z_90, estimate_total

UNCALIBRATED_WEIGHT_COL = "weight_uncal"
SYNTHETIC_COL = "is_synthetic_ultra_high"
REPLICATE_COLS: tuple[str, ...] = tuple(f"weight_r{r:02d}" for r in range(1, ACS_REPLICATES + 1))


def replicate_columns(df: pd.DataFrame) -> list[str]:
    """The ``weight_r01..weight_r80`` columns present on ``df``, in order."""
    return [c for c in REPLICATE_COLS if c in df.columns]


def snapshot_uncalibrated_weight(
    df: pd.DataFrame, *, weight_col: str = "weight", col: str = UNCALIBRATED_WEIGHT_COL,
) -> pd.DataFrame:
    """Copy ``weight`` into ``weight_uncal`` before anything calibrates it.

    Call once, on the cached units as loaded (the basis the replicate
    weights were built on). A frame that already carries the column is
    returned unchanged, so a second snapshot cannot overwrite the basis.
    """
    if col in df.columns:
        return df
    out = df.copy()
    out[col] = out[weight_col].astype(float)
    return out


def calibration_ratio(
    df: pd.DataFrame, *, weight_col: str = "weight", uncal_col: str = UNCALIBRATED_WEIGHT_COL,
) -> np.ndarray:
    """``weight / weight_uncal`` per row; 1.0 where there is no usable basis."""
    w = df[weight_col].to_numpy(dtype=float)
    if uncal_col not in df.columns:
        return np.ones(len(df), dtype=float)
    u = df[uncal_col].to_numpy(dtype=float)
    ok = np.isfinite(u) & (u > 0)
    ratio = np.ones(len(df), dtype=float)
    ratio[ok] = w[ok] / u[ok]
    return ratio


def replicate_weight_matrix(
    df: pd.DataFrame,
    *,
    weight_col: str = "weight",
    uncal_col: str = UNCALIBRATED_WEIGHT_COL,
    synthetic_col: str = SYNTHETIC_COL,
    replicate_cols: Optional[Sequence[str]] = None,
) -> np.ndarray:
    """The ``(n_units, 1 + R)`` weight matrix the SDR engine aggregates over.

    Column 0 is ``weight``; column ``r`` is ``weight_r x weight / weight_uncal``.
    Synthetic rows, rows without an uncalibrated basis and replicate cells
    that are not finite carry ``weight`` instead (no sampling variation).
    Raises when the frame has no replicate columns: the caller should have
    built the cache with replicate weights (``forecast_sb3125.py``).
    """
    rep_cols = list(replicate_cols) if replicate_cols is not None else replicate_columns(df)
    if not rep_cols:
        raise ValueError(
            "replicate_weight_matrix: no weight_r01..weight_r80 columns on the frame. "
            "Rebuild the tax-unit cache with replicate weights (forecast_sb3125.py "
            "loads them) or run with --no-replicate-se."
        )
    w = df[weight_col].to_numpy(dtype=float)
    ratio = calibration_ratio(df, weight_col=weight_col, uncal_col=uncal_col)
    has_basis = (
        np.isfinite(df[uncal_col].to_numpy(dtype=float)) & (df[uncal_col].to_numpy(dtype=float) > 0)
        if uncal_col in df.columns else np.zeros(len(df), dtype=bool)
    )
    synthetic = (
        df[synthetic_col].fillna(False).to_numpy(dtype=bool)
        if synthetic_col in df.columns else np.zeros(len(df), dtype=bool)
    )
    reps = df[rep_cols].to_numpy(dtype=float) * ratio[:, None]
    fixed = (~has_basis | synthetic)[:, None] | ~np.isfinite(reps)
    reps = np.where(fixed, w[:, None], reps)
    return np.column_stack([w, reps])


def sdr_totals(
    values: dict[str, np.ndarray],
    weight_matrix: np.ndarray,
    *,
    scale: float = 1.0,
    mask: Optional[np.ndarray] = None,
    z: float = Z_90,
) -> dict[str, SDREstimate]:
    """SDR estimate of ``Σ w v`` (times ``scale``) for each named per-unit vector.

    ``mask`` restricts the sum to a subset of rows (an AGI class, say); the
    replicate factor stays ``4 / R`` whatever the subset.
    """
    out: dict[str, SDREstimate] = {}
    W = weight_matrix if mask is None else weight_matrix[mask]
    for name, v in values.items():
        vv = np.asarray(v, dtype=float) if mask is None else np.asarray(v, dtype=float)[mask]
        est = estimate_total(vv * scale, W, z=z)
        out[name] = est
    return out


def sdr_columns(estimates: dict[str, SDREstimate], *, ndigits: Optional[int] = 3) -> dict[str, float]:
    """Flatten ``{name: SDREstimate}`` to ``name_se / name_ci90_low / name_ci90_high``
    columns (the point itself is left to the caller's own column)."""
    cols: dict[str, float] = {}
    for name, est in estimates.items():
        d = est.as_dict(name)
        d.pop(name)
        for k, v in d.items():
            cols[k] = round(v, ndigits) if ndigits is not None else v
    return cols


__all__ = [
    "REPLICATE_COLS",
    "SYNTHETIC_COL",
    "UNCALIBRATED_WEIGHT_COL",
    "calibration_ratio",
    "replicate_columns",
    "replicate_weight_matrix",
    "sdr_columns",
    "sdr_totals",
    "snapshot_uncalibrated_weight",
]
