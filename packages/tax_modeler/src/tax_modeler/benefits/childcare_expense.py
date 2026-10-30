"""SPM work-related childcare-expense imputation from CPS ASEC Hawaii donors.

The Supplemental Poverty Measure subtracts work-related child-care
expenses from family resources before threshold comparison (SPM_CAPHOUSEHLDR /
SPM_CHILDCAREXPNS in the CPS ASEC tables). PUMS doesn't carry these
amounts, so we impute from CPS ASEC Hawaii donor records.

Without this step, :func:`tax_modeler.poverty.spm.compute_spm_resources`
zeroes the ``childcare_expense_amount`` column and the SPM rate is biased
*downward* for working families with kids (resources overstated).

Imputation pattern mirrors :mod:`tax_modeler.benefits.moop` — family-
level cell-mean match by (n-kids-bin × income-quintile) with a two-level
fallback (n-kids alone, then global mean). Only working families with
children receive a non-zero imputation.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

_DEFAULT_DONOR_PATH = (
    Path(__file__).resolve().parents[1]
    / "data" / "cps_donor" / "cps_asec_hawaii.parquet"
)

_INCOME_QUINTILES = 5
_KID_BINS = [-1, 0, 1, 2, np.inf]
_KID_LABELS = ["0", "1", "2", "3+"]


def _aggregate_donor_families(donors_raw: pd.DataFrame) -> pd.DataFrame:
    """Collapse CPS ASEC person-rows to one row per SPM family.

    childcare_expense is a per-family CPS aggregate broadcast across SPM
    rows; ``.first()`` per ``PH_SEQ`` recovers the family-level value.
    """
    agg = donors_raw.groupby("PH_SEQ").agg(
        childcare_expense=("childcare_expense", "first"),
        income=("income", "first"),
        weight=("weight", "first"),
        n_kids=("SPM_NUMKIDS", "first"),
    )
    return agg.reset_index(drop=True)


def compute_childcare_expense_for_units(
    units: pd.DataFrame,
    *,
    donor_path: Path | None = None,
    out_col: str = "childcare_expense_amount",
    income_col: str = "total_cash_income",
    qualifying_children_col: str = "num_qualifying_children",
    earned_income_col: str = "earned_income",
    filing_status_col: str = "filing_status",
    secondary_hours_col: str = "secondary_hours_worked",
) -> pd.DataFrame:
    """Impute annual SPM work-related childcare expense at tax-unit level.

    Match recipients to donor cells on (n-kids × income-quintile) and
    assign the donor cell-mean. Falls back to n-kids alone, then a
    global mean. Filers with zero qualifying children or zero earned
    income get $0.

    Applies the Census SPM childcare cap (Short 2012; Renwick & Fox 2016):

      * single / HoH:                  cap = earned_income
      * MFJ, both spouses working:     cap ≈ 0.5 × earned_income
                                       (per-spouse earnings unavailable in PUMS;
                                       use balanced-split heuristic)
      * MFJ, only one spouse works:    cap = 0
                                       (Census disallows childcare deduction
                                       when one parent could provide care)

    The donor frame's ``SPM_CHILDCAREXPNS`` is uncapped, so without this
    step the model would over-subtract childcare for low-income MFJ
    families where the cap would bind, biasing the SPM rate upward.
    """
    path = Path(donor_path) if donor_path is not None else _DEFAULT_DONOR_PATH
    donors_raw = pd.read_parquet(path)
    donors = _aggregate_donor_families(donors_raw)

    inc_edges = (
        donors["income"].clip(lower=0).quantile(
            np.linspace(0, 1, _INCOME_QUINTILES + 1)
        ).to_numpy()
    )
    inc_edges = np.unique(inc_edges)
    inc_edges[0] = -np.inf
    inc_edges[-1] = np.inf

    donors["kid_bin"] = pd.cut(
        donors["n_kids"], bins=_KID_BINS, labels=_KID_LABELS, include_lowest=True,
    )
    donors["inc_bin"] = pd.cut(
        donors["income"], bins=inc_edges, labels=False, include_lowest=True,
    )

    cell = donors.groupby(["kid_bin", "inc_bin"], observed=True)["childcare_expense"].mean()
    kid_mean = donors.groupby("kid_bin", observed=True)["childcare_expense"].mean()
    # Global mean across families *with kids* — donors with zero kids
    # have zero childcare expense and would deflate the fallback.
    has_kids = donors["n_kids"].fillna(0) > 0
    global_mean = float(donors.loc[has_kids, "childcare_expense"].mean()) if has_kids.any() else 0.0

    out = units.copy()
    income = out.get(income_col, pd.Series(0.0, index=out.index)).fillna(0).astype(float).clip(lower=0)
    n_kids = out.get(qualifying_children_col, pd.Series(0, index=out.index)).fillna(0).clip(lower=0).astype(int).to_numpy()
    earned = out.get(earned_income_col, pd.Series(0.0, index=out.index)).fillna(0).astype(float).to_numpy()

    kid_bin = pd.cut(n_kids, bins=_KID_BINS, labels=_KID_LABELS, include_lowest=True)
    inc_bin = pd.cut(income, bins=inc_edges, labels=False, include_lowest=True)

    amt = np.full(len(out), global_mean, dtype=float)
    cell_lookup = cell.to_dict()
    kid_lookup = kid_mean.to_dict()
    for i, (kb, ib) in enumerate(zip(kid_bin, inc_bin)):
        v = cell_lookup.get((kb, ib))
        if v is not None and not pd.isna(v):
            amt[i] = v
            continue
        v2 = kid_lookup.get(kb)
        if v2 is not None and not pd.isna(v2):
            amt[i] = v2

    # SPM gates childcare expense to working families with kids.
    working_with_kids = (n_kids > 0) & (earned > 0)
    amt = np.where(working_with_kids, amt, 0.0)

    # Apply Census SPM childcare cap (lower-earner's earnings).
    is_joint = (out[filing_status_col].astype(str) == "married_filing_jointly").to_numpy()
    if secondary_hours_col in out.columns:
        secondary_working = out[secondary_hours_col].fillna(0).astype(float).to_numpy() > 0
    else:
        # Without a secondary-hours signal, assume MFJ has only one earner.
        # That's conservative (allows zero childcare deduction for MFJ).
        secondary_working = np.zeros(len(out), dtype=bool)
    cap = np.where(
        is_joint,
        np.where(secondary_working, 0.5 * earned, 0.0),
        earned,
    )
    amt = np.minimum(amt, cap)
    out[out_col] = np.maximum(amt, 0.0)
    return out


__all__ = ["compute_childcare_expense_for_units"]
