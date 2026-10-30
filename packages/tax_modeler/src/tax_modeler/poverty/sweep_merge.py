"""Merge poverty_impact_sweep.py output into the headline poverty-impact CSVs.

The sweep harness emits one ``by_state.csv`` per swept cell plus a
``param_ranges.csv`` that aggregates min/max/median per output column
across all cells. This module reads that param-range CSV and splices
``<col>_param_min`` / ``<col>_param_max`` / ``<col>_param_median`` columns
into the headline by_state / by_county / by_house_district /
by_senate_district / by_household_type DataFrames emitted by
:func:`compute_poverty_impact`.

Important: the sweep is currently run only at state granularity (the
sweep script collates ``by_state.csv`` per cell). District-level
sensitivity sweeps are a separate (heavier) workflow not yet built. So
only ``by_state`` receives populated ``*_param_*`` columns; the other
aggregates receive NaN-filled columns for schema parity.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _splice_param_cols(
    *,
    df: pd.DataFrame,
    param_ranges: pd.DataFrame,
    fill_value: float = float("nan"),
) -> pd.DataFrame:
    """Append ``<col>_param_min``/``_param_max``/``_param_median`` to ``df``.

    For columns present in both ``df`` and ``param_ranges``, splice the
    sweep ranges in. For columns absent from the sweep, emit NaN-filled
    siblings so downstream consumers can rely on the schema being
    uniform across geographies.
    """
    out = df.copy()
    range_index = param_ranges.set_index("column")
    for col in df.columns:
        if not (col.startswith("persons_lifted_") or col.startswith("poverty_rate_")):
            continue
        if col.endswith("_se"):
            continue  # SDR SE columns aren't sweep targets.
        suffixes = ("param_min", "param_max", "param_median")
        if col in range_index.index:
            for suf in suffixes:
                source = f"{suf}" if suf != "param_median" else "param_median"
                out[f"{col}_{suf}"] = range_index.loc[col, source]
        else:
            for suf in suffixes:
                out[f"{col}_{suf}"] = fill_value
    return out


def merge_sweep_param_ranges(
    *,
    sweep_csv: Path,
    by_state: pd.DataFrame,
    by_county: pd.DataFrame,
    by_house_district: pd.DataFrame,
    by_senate_district: pd.DataFrame,
    by_household_type: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read ``param_ranges.csv`` and splice ``*_param_*`` into each aggregate.

    Parameters
    ----------
    sweep_csv:
        Path to a ``param_ranges.csv`` produced by
        :file:`scripts/poverty_impact_sweep.py`. The file is expected to
        have at least the columns ``column``, ``param_min``, ``param_max``,
        ``param_median``.
    by_state .. by_household_type:
        The five headline DataFrames returned by
        :func:`compute_poverty_impact`.

    Returns
    -------
    tuple of DataFrames
        Same five frames in the same order, each with
        ``<col>_param_min`` / ``<col>_param_max`` / ``<col>_param_median``
        columns appended. State-level cells use the sweep's actual ranges;
        all other geographies receive NaN-filled siblings for schema
        parity (until per-district sweeps are wired).
    """
    sweep_csv = Path(sweep_csv)
    if not sweep_csv.exists():
        raise FileNotFoundError(f"--merge-sweep target {sweep_csv} not found")
    param_ranges = pd.read_csv(sweep_csv)
    required = {"column", "param_min", "param_max", "param_median"}
    missing = required - set(param_ranges.columns)
    if missing:
        raise ValueError(
            f"{sweep_csv} is missing required columns {sorted(missing)}; "
            "is this a sweep param_ranges.csv?"
        )

    by_state_out = _splice_param_cols(df=by_state, param_ranges=param_ranges)
    # Geographies other than state get NaN-filled param columns for now —
    # the sweep harness only collates state rows. Future work: add
    # by_county.csv (etc.) collation to the sweep and feed those tables in.
    empty_ranges = param_ranges.iloc[0:0]
    by_county_out = _splice_param_cols(df=by_county, param_ranges=empty_ranges)
    by_hd_out = _splice_param_cols(df=by_house_district, param_ranges=empty_ranges)
    by_sd_out = _splice_param_cols(df=by_senate_district, param_ranges=empty_ranges)
    by_ht_out = _splice_param_cols(df=by_household_type, param_ranges=empty_ranges)

    logger.info(
        "Merged sweep param ranges from %s into by_state (%d cells); "
        "other geographies received NaN-filled siblings.",
        sweep_csv, len(param_ranges),
    )
    return by_state_out, by_county_out, by_hd_out, by_sd_out, by_ht_out


__all__ = ["merge_sweep_param_ranges"]
