"""One weight basis for poverty and revenue: the tax-unit calibration ratio.

The SPM aggregator (:func:`tax_modeler.poverty.spm_aggregation.aggregate_to_spm_units`)
weights each SPM unit by the raw household ``WGTP`` and discards the
calibrated tax-unit ``weight``: the constructor's filing-status factors
(single 0.82, HoH 1.30, MFS 1.35 — ``units.constructor``), the EITC
by-children reweight (``calibration.eitc_reweight``) and, on the revenue
path, the IPF rake. So a reform's revenue score and its poverty score have
been weighting the same unit differently.

:func:`tax_unit_calibration_ratio` is the per-unit ratio of the calibrated
weight to the weight the PUMS gave the unit (the hybrid rule's PWGTP for a
one-person unit, WGTP otherwise, before any factor), and the aggregator can
carry its mean over an SPM unit's tax units onto the household weight and
each replicate weight::

    weight_spm = WGTP x mean_{tax units in the SPM unit}(weight / weight_pums)

The same ratio scales ``WGTP1..80`` so the SDR band stays on the carried
basis. Whether this basis is the default is an empirical question (how far
it moves the published rate); see SB3125_CD1_FORECAST.md.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

CALIBRATION_RATIO_COL = "calibration_ratio"


def pums_weight(units: pd.DataFrame) -> np.ndarray:
    """The uncalibrated PUMS weight behind each tax unit's ``weight``.

    Mirrors ``TaxUnitConstructor._calculate_hybrid_weight`` before its
    filing-status factor: a one-person unit (no spouse, no dependents) is
    weighted by its filer's PWGTP (``person_weight_sum`` holds that one
    weight), any larger unit by the household WGTP (``hh_weight``). A frame
    without those columns but with ``weight_uncal`` (the revenue path's
    snapshot) uses that; otherwise NaN, and the ratio is 1.
    """
    n = len(units)
    out = np.full(n, np.nan, dtype=float)
    if {"hh_weight", "person_weight_sum", "filing_status"} <= set(units.columns):
        joint = (units["filing_status"].to_numpy() == "married_filing_jointly")
        deps = units["num_dependents"].fillna(0).to_numpy(dtype=float) if "num_dependents" in units.columns \
            else np.zeros(n)
        multi = joint | (deps > 0)
        hh = units["hh_weight"].to_numpy(dtype=float)
        pw = units["person_weight_sum"].to_numpy(dtype=float)
        out = np.where(multi, hh, pw)
    elif "weight_uncal" in units.columns:
        out = units["weight_uncal"].to_numpy(dtype=float)
    return out


def tax_unit_calibration_ratio(units: pd.DataFrame, *, weight_col: str = "weight") -> np.ndarray:
    """``weight / pums_weight`` per tax unit; 1.0 where there is no PUMS basis
    (synthetic rows, fixtures without the weight columns)."""
    w = units[weight_col].to_numpy(dtype=float)
    base = pums_weight(units)
    ok = np.isfinite(base) & (base > 0)
    ratio = np.ones(len(units), dtype=float)
    ratio[ok] = w[ok] / base[ok]
    return ratio


def attach_calibration_ratio(units: pd.DataFrame, *, col: str = CALIBRATION_RATIO_COL) -> pd.DataFrame:
    """``units`` with the :func:`tax_unit_calibration_ratio` as column ``col``."""
    out = units.copy()
    out[col] = tax_unit_calibration_ratio(units)
    return out


__all__ = [
    "CALIBRATION_RATIO_COL",
    "attach_calibration_ratio",
    "pums_weight",
    "tax_unit_calibration_ratio",
]
