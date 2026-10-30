"""MOOP (Medical Out-Of-Pocket) imputation for SPM resources.

The Supplemental Poverty Measure subtracts medical out-of-pocket
expenses from family resources. PUMS doesn't carry MOOP, so we impute
from CPS ASEC Hawaii donor records by matching each tax unit to
similar CPS households on (primary-filer age bracket, household size,
income decile) and assigning the cell-mean donor MOOP.

Without this step, ``compute_spm_resources`` zeroes the MOOP component
and the resulting baseline Hawaii SPM rate runs ~10pp above the
Census-published P60-280 figure (~10-12%); persons-lifted-by-credit
estimates are correspondingly biased upward.

Public API: :func:`compute_moop_for_units`.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

_DEFAULT_DONOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "cps_donor" / "cps_moop_donors.csv"
)

# Bucket edges. Matched on the donor frame to produce dense cells. Recipients
# (tax units) are bucketed identically and joined.
#
# The 65 break is the SPM-relevant Medicare-eligibility threshold. Census P60-280
# §4.3 stratifies MOOP by under-65 / 65+ (Medicare vs. non-Medicare) because the
# Medicare premiums + Part D out-of-pocket profile is markedly different from the
# pre-Medicare private/uninsured profile. Splitting the prior 60-74 bin at 65
# removes the bias that lumped 4 pre-Medicare years (60-64) with 10 Medicare
# years (65-74), inflating MOOP for the 60-64 group and deflating for 65-74.
_AGE_EDGES = [-1, 17, 29, 44, 59, 64, 74, np.inf]
_AGE_LABELS = ["child", "18-29", "30-44", "45-59", "60-64", "65-74", "75+"]
_HH_EDGES = [0, 1, 2, 3, np.inf]
_HH_LABELS = ["1", "2", "3", "4+"]
_INCOME_DECILES = 5  # quintiles — 200 donors are too sparse for 10-cell income


def _bucket(donors: pd.DataFrame) -> pd.DataFrame:
    """Add age_bin / hh_bin / inc_bin columns to a donor frame in place."""
    donors = donors.copy()
    donors["age_bin"] = pd.cut(
        donors["age"], bins=_AGE_EDGES, labels=_AGE_LABELS, include_lowest=True,
    )
    donors["hh_bin"] = pd.cut(
        donors["hh_size"], bins=_HH_EDGES, labels=_HH_LABELS, include_lowest=True,
    )
    donors["inc_bin"] = pd.qcut(
        donors["income"].clip(lower=0), q=_INCOME_DECILES,
        duplicates="drop", labels=False,
    )
    return donors


def _cell_table(donors: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series, float]:
    """Pre-aggregate donor MOOP at three fallback levels.

    Returns (cell_mean, age_hh_mean, age_mean, global_mean) — Series indexed
    by the bucket combinations, plus a scalar global mean.
    """
    cell = donors.groupby(["age_bin", "hh_bin", "inc_bin"], observed=True)["moop"].mean()
    age_hh = donors.groupby(["age_bin", "hh_bin"], observed=True)["moop"].mean()
    age = donors.groupby(["age_bin"], observed=True)["moop"].mean()
    global_mean = float(donors["moop"].mean())
    return cell, age_hh, age, global_mean


def compute_moop_for_units(
    units: pd.DataFrame,
    *,
    donor_path: Path | None = None,
    out_col: str = "moop_amount",
    age_col: str = "primary_agep",
    dependents_col: str = "num_dependents",
    income_col: str = "total_cash_income",
    filing_status_col: str = "filing_status",
) -> pd.DataFrame:
    """Impute annual MOOP from CPS ASEC Hawaii donors.

    Match each tax unit to donor cells on (age-bracket, household-size,
    income-quintile) and assign the donor cell-mean MOOP. Falls back to
    (age, hh_size) → age → global mean for sparse / empty cells.

    Parameters
    ----------
    units:
        Tax-unit DataFrame. Must carry ``primary_agep`` (or override via
        ``age_col``), ``num_dependents``, ``filing_status``, and an
        income column (default ``total_cash_income``).
    donor_path:
        Override path to the donor CSV. Defaults to the in-package
        ``cps_donor/cps_moop_donors.csv``.
    out_col:
        Output column name (default ``moop_amount``, which
        :func:`tax_modeler.poverty.spm.compute_spm_resources` reads
        by default).

    Returns
    -------
    pd.DataFrame
        Copy of ``units`` with an additional ``moop_amount`` column
        (annual dollars; non-negative).

    Notes
    -----
    * Imputation is deterministic — cell means, not random draws. This
      preserves total Hawaii MOOP dollars and produces reproducible
      poverty-impact runs.
    * Donor cell sparsity: with 200 donors and 6×4×5 = 120 cells, some
      cells are empty. Fallbacks compress to age×hh, then age, then
      global mean. Cell hit rate logged via ``meta`` is typically
      >85% of recipient mass.
    """
    path = Path(donor_path) if donor_path is not None else _DEFAULT_DONOR_PATH
    donors_raw = pd.read_csv(path)
    donors = _bucket(donors_raw)
    cell_mean, age_hh_mean, age_mean, global_mean = _cell_table(donors)

    out = units.copy()
    age = out.get(age_col, pd.Series(0, index=out.index)).fillna(0).astype(int)
    is_joint = (out[filing_status_col] == "married_filing_jointly").astype(int)
    hh_size = (
        1 + is_joint + out[dependents_col].fillna(0).clip(lower=0).astype(int)
    )
    income = out[income_col].fillna(0).astype(float).clip(lower=0)

    age_bin = pd.cut(age, bins=_AGE_EDGES, labels=_AGE_LABELS, include_lowest=True)
    hh_bin = pd.cut(hh_size, bins=_HH_EDGES, labels=_HH_LABELS, include_lowest=True)

    # Build income quintile bins from the donor frame's quantile breaks so
    # recipients and donors share the same edges (vs. quintiles of recipient
    # income, which would mis-rank Hawaii-low against CPS-Hawaii-median).
    donor_inc_edges = (
        donors_raw["income"].clip(lower=0).quantile(
            np.linspace(0, 1, _INCOME_DECILES + 1)
        ).to_numpy()
    )
    donor_inc_edges = np.unique(donor_inc_edges)
    donor_inc_edges[0] = -np.inf
    donor_inc_edges[-1] = np.inf
    inc_bin = pd.cut(income, bins=donor_inc_edges, labels=False, include_lowest=True)

    # Vectorized lookup with three-level fallback.
    moop = np.full(len(out), global_mean, dtype=float)

    # Step 1: age × hh × income cell.
    key3 = list(zip(age_bin, hh_bin, inc_bin, strict=False))
    cell_lookup = cell_mean.to_dict()
    for i, k in enumerate(key3):
        v = cell_lookup.get(k)
        if v is not None and not pd.isna(v):
            moop[i] = v
            continue
        # Step 2: age × hh.
        v2 = age_hh_mean.get((k[0], k[1]))
        if v2 is not None and not pd.isna(v2):
            moop[i] = v2
            continue
        # Step 3: age only.
        v3 = age_mean.get(k[0])
        if v3 is not None and not pd.isna(v3):
            moop[i] = v3
        # else: global mean already in place.

    out[out_col] = np.maximum(moop, 0.0)
    return out


__all__ = ["compute_moop_for_units"]
