"""Run a per-unit function over every row of a tax-unit frame.

The scalar calculators (Hawaiʻi tax, EITC, CTC) take one unit as a dict and
return the columns they compute. Their loops used to rebuild the whole frame
from those dicts on every pass; :func:`apply_per_unit` builds only the columns
the function returned and keeps the rest as they are, which returns the same
frame for a fraction of the cost.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

import pandas as pd

# Columns of these dtypes come back from a dict round trip unchanged, so they
# are kept; any other column (object, nullable, categorical, ...) is rebuilt
# from its values, as the round trip would infer it.
_KEPT_DTYPES = frozenset({"float64", "int64", "bool", "str"})


def unit_dicts(df: pd.DataFrame) -> list[dict]:
    """``df.to_dict("records")``: the same dicts, built column by column."""
    cols = list(df.columns)
    return [dict(zip(cols, row, strict=True)) for row in zip(*(df[c].tolist() for c in cols), strict=True)]


def apply_per_unit(df: pd.DataFrame, fn: Callable[[dict], Mapping]) -> pd.DataFrame:
    """``pd.DataFrame([{**u, **fn(u)} for u in df.to_dict("records")])``.

    Same columns in the same order, values and dtypes, and a fresh RangeIndex.
    ``fn`` must not modify the dict it is given.
    """
    units = unit_dicts(df)
    results = [fn(u) for u in units]
    keys = list(dict.fromkeys(k for r in results for k in r))
    cols = list(df.columns)
    if any(r.keys() != results[0].keys() for r in results):
        # A unit that leaves a column out keeps its own value for it.
        rows = [{k: r[k] if k in r else u.get(k, float("nan")) for k in keys}
                for u, r in zip(units, results, strict=True)]
    else:
        rows = results
    computed = pd.DataFrame(rows, columns=keys) if rows else pd.DataFrame(columns=keys)
    index = pd.RangeIndex(len(df))
    out = {}
    for c in cols:
        if c in computed:
            out[c] = computed[c]
        elif str(df[c].dtype) in _KEPT_DTYPES:
            out[c] = pd.Series(df[c].to_numpy(copy=False) if str(df[c].dtype) != "str" else df[c].array,
                               index=index, name=c)
        else:
            out[c] = pd.DataFrame({c: [u[c] for u in units]})[c]
    for k in keys:
        if k not in out:
            out[k] = computed[k]
    return pd.DataFrame(out, index=index)
