"""Run a per-unit function over every row of a tax-unit frame.

The scalar calculators (Hawaiʻi tax, EITC, CTC) take one unit as a dict and
return the columns they compute. Their loops used to rebuild the whole frame
from those dicts on every pass; :func:`apply_per_unit` builds only the columns
the function returned and keeps the rest as they are, which returns the same
frame for a fraction of the cost.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from typing import Any

import numpy as np
import pandas as pd

# Columns of these dtypes come back from a dict round trip unchanged, so they
# are kept; any other column (object, nullable, categorical, ...) is rebuilt
# from its values, as the round trip would infer it.
_KEPT_DTYPES = frozenset({"float64", "int64", "bool", "str"})


def unit_dicts(df: pd.DataFrame) -> list[dict]:
    """``df.to_dict("records")``: the same dicts, built column by column."""
    cols = list(df.columns)
    return [dict(zip(cols, row, strict=True)) for row in zip(*(df[c].tolist() for c in cols), strict=True)]


class _Columns:
    """A frame's columns as Python lists, each built the first time a row reads it."""

    __slots__ = ("df", "names", "lists")

    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.names = list(df.columns)
        self.lists: dict[Any, list] = {}

    def column(self, key: Any) -> list:
        try:
            return self.lists[key]
        except KeyError:
            if key not in self.df.columns:
                raise
            values = self.lists[key] = self.df[key].tolist()
            return values


class UnitRow(Mapping):
    """One row of a frame as a read-only mapping: the same keys and values as
    its ``to_dict("records")`` dict, but a column is only built into Python
    values when some row reads it. The calculators read a few of a unit's
    100-odd columns, so this skips building the rest."""

    __slots__ = ("_cols", "_i")

    def __init__(self, cols: _Columns, i: int):
        self._cols = cols
        self._i = i

    def __getitem__(self, key: Any) -> Any:
        return self._cols.column(key)[self._i]

    def get(self, key: Any, default: Any = None) -> Any:
        try:
            return self._cols.column(key)[self._i]
        except KeyError:
            return default

    def __contains__(self, key: Any) -> bool:
        return key in self._cols.lists or key in self._cols.df.columns

    def __iter__(self) -> Iterator:
        return iter(self._cols.names)

    def __len__(self) -> int:
        return len(self._cols.names)


def unit_rows(df: pd.DataFrame) -> list[Mapping]:
    """The rows of ``df`` as :class:`UnitRow` mappings (plain dicts from
    :func:`unit_dicts` if its column names repeat, where a mapping cannot say
    which one a key means)."""
    if not df.columns.is_unique:
        return unit_dicts(df)
    cols = _Columns(df)
    return [UnitRow(cols, i) for i in range(len(df))]


def apply_per_unit(df: pd.DataFrame, fn: Callable[[Mapping], Mapping]) -> pd.DataFrame:
    """``pd.DataFrame([{**u, **fn(u)} for u in df.to_dict("records")])``.

    Same columns in the same order, values and dtypes, and a fresh RangeIndex.
    ``fn`` is given each unit as a read-only mapping (:func:`unit_rows`).
    """
    units = unit_rows(df)
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


def groups_by_value(values) -> list[tuple[Any, np.ndarray]]:
    """``[(v, values == v) for v in np.unique(values)]``: each distinct value,
    in sorted order, with the mask of the rows that hold it.

    Built by hashing (``pd.factorize``) rather than ``np.unique``, which sorts
    the whole array: for an array of filing-status strings that sort was most
    of the scoring kernel's time. Arrays with missing values take the
    ``np.unique`` path, so they behave exactly as before.
    """
    values = np.asarray(values)
    codes, uniques = pd.factorize(values)
    if (codes < 0).any():
        return [(v, values == v) for v in np.unique(values)]
    order = sorted(range(len(uniques)), key=lambda k: uniques[k])
    return [(uniques[k], codes == k) for k in order]
