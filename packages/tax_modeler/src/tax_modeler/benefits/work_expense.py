"""SPM work-expense imputation from CPS ASEC Hawaii donors.

The Supplemental Poverty Measure subtracts other-work expenses (commuting,
uniforms, union dues, etc.) from family resources before threshold
comparison. PUMS doesn't carry the SPM work-expense component, so we
impute from CPS ASEC Hawaii donor records.

Without this step, :func:`tax_modeler.poverty.spm.compute_spm_resources`
zeroes the ``work_expense_amount`` column and the resulting SPM rate is
biased *downward* for working low-income families (resources overstated).

Imputation pattern mirrors :mod:`tax_modeler.benefits.moop` — family-
level cell-mean match by (income-quintile × n-earners) with a two-level
fallback (n-earners-only, then global mean). Donor work_expense is a
per-family CPS value broadcast across persons in the same SPM unit;
aggregating with ``.first()`` per ``PH_SEQ`` recovers the family-level
amount.
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
_EARNER_BINS = [-1, 0, 1, 2, np.inf]
_EARNER_LABELS = ["0", "1", "2", "3+"]


def _aggregate_donor_families(donors_raw: pd.DataFrame) -> pd.DataFrame:
    """Collapse CPS ASEC person-rows to one row per SPM family.

    CPS work_expense is family-level; aggregate by ``PH_SEQ`` taking the
    first value (broadcast across all persons in the SPM unit). Count
    earners by summing ``wage_income > 0`` indicators.
    """
    agg = donors_raw.groupby("PH_SEQ").agg(
        work_expense=("work_expense", "first"),
        income=("income", "first"),
        weight=("weight", "first"),
    )
    agg["n_earners"] = (
        donors_raw.assign(_earn=(donors_raw["wage_income"] > 0).astype(int))
        .groupby("PH_SEQ")["_earn"].sum()
    )
    return agg.reset_index(drop=True)


def compute_work_expense_for_units(
    units: pd.DataFrame,
    *,
    donor_path: Path | None = None,
    out_col: str = "work_expense_amount",
    income_col: str = "total_cash_income",
    earned_income_col: str = "earned_income",
    filing_status_col: str = "filing_status",
) -> pd.DataFrame:
    """Impute annual SPM work expense at tax-unit level.

    Match recipients to donor cells on (n-earners × income-quintile) and
    assign the donor cell-mean work expense. Falls back to n-earners
    alone, then a global mean. Recipients with zero earners get $0.

    Recipient n-earners is derived from ``filing_status`` plus working
    signals (``primary_hours_worked``, ``secondary_hours_worked`` when
    available; otherwise from positive ``earned_income``).
    """
    path = Path(donor_path) if donor_path is not None else _DEFAULT_DONOR_PATH
    donors_raw = pd.read_parquet(path)
    donors = _aggregate_donor_families(donors_raw)

    # Donor income-quintile edges (shared with recipients).
    inc_edges = (
        donors["income"].clip(lower=0).quantile(
            np.linspace(0, 1, _INCOME_QUINTILES + 1)
        ).to_numpy()
    )
    inc_edges = np.unique(inc_edges)
    inc_edges[0] = -np.inf
    inc_edges[-1] = np.inf

    donors["earner_bin"] = pd.cut(
        donors["n_earners"], bins=_EARNER_BINS, labels=_EARNER_LABELS,
        include_lowest=True,
    )
    donors["inc_bin"] = pd.cut(
        donors["income"], bins=inc_edges, labels=False, include_lowest=True,
    )

    cell = donors.groupby(["earner_bin", "inc_bin"], observed=True)["work_expense"].mean()
    earner_mean = donors.groupby("earner_bin", observed=True)["work_expense"].mean()
    global_mean = float(donors["work_expense"].mean())

    out = units.copy()
    income = out.get(income_col, pd.Series(0.0, index=out.index)).fillna(0).astype(float).clip(lower=0)
    earned = out.get(earned_income_col, pd.Series(0.0, index=out.index)).fillna(0).astype(float)
    is_joint = (out[filing_status_col] == "married_filing_jointly").to_numpy()

    if "primary_hours_worked" in out.columns:
        primary_working = out["primary_hours_worked"].fillna(0).astype(float).to_numpy() > 0
    else:
        primary_working = earned.to_numpy() > 0
    if "secondary_hours_worked" in out.columns:
        secondary_working = out["secondary_hours_worked"].fillna(0).astype(float).to_numpy() > 0
    else:
        # Without a secondary signal we cannot detect a second earner on MFJ;
        # the CPS donor cells still mostly cluster at n_earners=1 or 2.
        secondary_working = np.zeros(len(out), dtype=bool)
    n_earners = primary_working.astype(int) + (is_joint & secondary_working).astype(int)

    earner_bin = pd.cut(
        n_earners, bins=_EARNER_BINS, labels=_EARNER_LABELS, include_lowest=True,
    )
    inc_bin = pd.cut(income, bins=inc_edges, labels=False, include_lowest=True)

    moop = np.full(len(out), global_mean, dtype=float)
    cell_lookup = cell.to_dict()
    earner_lookup = earner_mean.to_dict()
    for i, (eb, ib) in enumerate(zip(earner_bin, inc_bin)):
        v = cell_lookup.get((eb, ib))
        if v is not None and not pd.isna(v):
            moop[i] = v
            continue
        v2 = earner_lookup.get(eb)
        if v2 is not None and not pd.isna(v2):
            moop[i] = v2

    moop = np.where(n_earners > 0, moop, 0.0)
    out[out_col] = np.maximum(moop, 0.0)
    return out


__all__ = ["compute_work_expense_for_units"]
