"""Run a per-unit function over every row of a tax-unit frame.

The scalar calculators (Hawaiʻi tax, EITC, CTC) take one unit as a dict and
return the columns they compute. Their loops used to rebuild the whole frame
from those dicts on every pass; :func:`apply_per_unit` builds only the columns
the function returned and keeps the rest as they are, which returns the same
frame for a fraction of the cost.

The same three calculators also have array versions, which compute every
unit at once and return what :func:`apply_per_unit` would have, bit for bit:
:func:`apply_columns` runs them (falling back to the scalar calculator) and
assembles their frame the same way, and
:func:`py_max`, :func:`py_min`, :func:`py_round`, :func:`or_zero` and
:func:`first_present` are the scalar code's builtins and idioms over arrays,
with the same results for NaN, signed zeros and ties.
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
    if any(r.keys() != results[0].keys() for r in results):
        # A unit that leaves a column out keeps its own value for it.
        rows = [{k: r[k] if k in r else u.get(k, float("nan")) for k in keys}
                for u, r in zip(units, results, strict=True)]
    else:
        rows = results
    computed = pd.DataFrame(rows, columns=keys) if rows else pd.DataFrame(columns=keys)
    return _assemble(df, computed, lambda c: [u[c] for u in units])


def _assemble(df: pd.DataFrame, computed: pd.DataFrame,
              values: Callable[[Any], list]) -> pd.DataFrame:
    """``df`` with ``computed``'s columns, as :func:`apply_per_unit` returns it.
    ``values(c)`` is column ``c``'s values as a list, for the columns whose
    dtype a dict round trip would infer again."""
    index = pd.RangeIndex(len(df))
    out = {}
    for c in df.columns:
        if c in computed:
            out[c] = computed[c]
        elif str(df[c].dtype) in _KEPT_DTYPES:
            out[c] = pd.Series(df[c].to_numpy(copy=False) if str(df[c].dtype) != "str" else df[c].array,
                               index=index, name=c)
        else:
            out[c] = pd.DataFrame({c: values(c)})[c]
    for k in computed.columns:
        if k not in out:
            out[k] = computed[k]
    return pd.DataFrame(out, index=index)


#: True sends every apply_columns call to the scalar calculator: the reference
#: run the parity tests compare the array versions with.
FORCE_PER_UNIT = False


class NotVectorizable(Exception):
    """Raised by an array calculator (or the column readers below) for a
    frame it does not handle; :func:`apply_columns` then runs the scalar
    calculator on each unit instead."""


def apply_columns(df: pd.DataFrame, arrays: Callable[[pd.DataFrame], Mapping[str, np.ndarray]],
                  fn: Callable[[Mapping], Mapping]) -> pd.DataFrame:
    """:func:`apply_per_unit` (``df``, ``fn``), computed by ``arrays`` when it can.

    ``arrays(df)`` returns the columns ``fn`` computes, for every unit at once,
    in the order ``fn``'s dict lists them, each with the dtype a frame of those
    dicts infers (int64 counts, bool flags, float64 amounts), or raises
    :class:`NotVectorizable`. An empty frame, or one with repeated column
    names, goes straight to ``fn``, which treats it as it always has. The
    frame is assembled exactly as :func:`apply_per_unit` assembles it.
    """
    if len(df) and df.columns.is_unique and not FORCE_PER_UNIT:
        try:
            columns = arrays(df)
        except NotVectorizable:
            pass
        else:
            return _assemble(df, pd.DataFrame(dict(columns)), lambda c: df[c].tolist())
    return apply_per_unit(df, fn)


# ---------------------------------------------------------------------------
# The scalar calculators' builtins, over arrays
# ---------------------------------------------------------------------------

def py_max(a, b) -> np.ndarray:
    """``max(a, b)`` per element: ``b`` only where ``b > a``, so a NaN or a
    tie (``max(0.0, -0.0)`` is ``0.0``) keeps ``a``, as the builtin does."""
    return np.where(b > a, b, a)


def py_min(a, b) -> np.ndarray:
    """``min(a, b)`` per element: ``b`` only where ``b < a``."""
    return np.where(b < a, b, a)


def py_round(x: np.ndarray, ndigits: int) -> np.ndarray:
    """``round(v, ndigits)`` for each float in ``x``, exactly.

    Python rounds the exact binary value half to even and returns the double
    nearest the rounded decimal. ``rint(x * 10**n) / 10**n`` does the same
    except where ``x * 10**n`` lands within an ulp of a half (the product's
    rounding can then cross it) or is too large to hold every integer; those
    few values, and NaN and infinities, go through ``round`` itself.
    """
    x = np.asarray(x, dtype=float)
    scale = 10.0 ** ndigits
    y = x * scale
    with np.errstate(invalid="ignore"):
        out = np.rint(y) / scale
        frac = np.abs(y - np.trunc(y))
        exact = (np.abs(y) < 2.0 ** 52) & (np.abs(frac - 0.5) > 4 * np.spacing(np.abs(y)))
    for i in np.flatnonzero(~exact):
        out[i] = round(float(x[i]), ndigits)
    return out


def or_zero(values: np.ndarray) -> np.ndarray:
    """``float(v or 0)`` for each float: a zero of either sign is 0.0, NaN stays NaN."""
    return np.where(values == 0.0, 0.0, values)


def first_present(df: pd.DataFrame, keys, default: float = 0.0) -> np.ndarray:
    """The scalar ``_first_present``: per unit, the first of ``keys`` whose
    value is not missing (NaN), as a float, else ``default``. Raises
    :class:`NotVectorizable` for a column that is not float, int or bool."""
    out = np.full(len(df), default, dtype=float)
    todo = np.ones(len(df), dtype=bool)
    for key in keys:
        if key not in df.columns:
            continue
        values = numeric_column(df, key)
        take = todo & ~np.isnan(values)
        out[take] = values[take]
        todo &= ~take
    return out


def status_column(df: pd.DataFrame, key: str = "filing_status",
                  default: str = "single") -> np.ndarray:
    """Column ``key`` as an object array (every unit ``default`` when the
    frame has no such column). Raises :class:`NotVectorizable` when it holds
    ``pd.NA``: comparing that raises, and the scalar code compares it only
    on the units it reaches."""
    if key not in df.columns:
        return np.full(len(df), default, dtype=object)
    values = df[key].to_numpy(dtype=object)
    if df[key].hasnans and any(v is pd.NA for v in values):
        raise NotVectorizable(f"pd.NA in {key}")
    return values


def numeric_column(df: pd.DataFrame, key, default: float | None = None) -> np.ndarray:
    """Column ``key`` as float64 if it holds a numpy float, int or bool dtype
    (whose values convert exactly, as ``tolist`` converts them for the scalar
    code); ``default`` for every unit when the frame has no such column and a
    default is given. Raises :class:`NotVectorizable` otherwise: pandas'
    nullable dtypes hold ``pd.NA``, which the scalar code treats differently
    from NaN."""
    if default is not None and key not in df.columns:
        return np.full(len(df), default, dtype=float)
    dtype = df[key].dtype
    if not isinstance(dtype, np.dtype) or dtype.kind not in "iufb":
        raise NotVectorizable(f"{key} is {dtype}")
    return df[key].to_numpy(dtype=float)


def group_sums(values: np.ndarray, codes: np.ndarray, n_groups: int) -> np.ndarray:
    """``[pd.Series(values[codes == g]).sum() for g in range(n_groups)]``,
    bit for bit, without a Python call per group.

    ``Series.sum`` replaces NaN with 0 and sums with numpy's pairwise sum, so a
    group's total depends on its values' order and count (``np.add.reduceat``,
    which adds in sequence, differs in the last bits). Groups of the same size
    are gathered, in their original order, into the rows of one contiguous
    matrix, whose row sums run that same summation. Empty groups sum to 0.0.
    """
    values = np.where(np.isnan(values), 0.0, np.asarray(values, dtype=float))
    codes = np.asarray(codes)
    order = np.argsort(codes, kind="stable")
    sizes = np.bincount(codes, minlength=n_groups)
    starts = np.concatenate(([0], np.cumsum(sizes)[:-1]))
    grouped = values[order]
    out = np.zeros(n_groups)
    for k in np.unique(sizes[sizes > 0]):
        groups = np.flatnonzero(sizes == k)
        out[groups] = grouped[starts[groups][:, None] + np.arange(k)].sum(axis=1)
    return out


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
