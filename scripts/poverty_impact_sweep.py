#!/usr/bin/env python3
"""Hawaii poverty-impact parameter-sensitivity sweep harness.

Drives ``scripts/poverty_impact_report.py`` repeatedly with one parameter
varied at a time across LOW/MID/HIGH cells (or, with ``--factorial``, the
full 3^5 = 243-cell Cartesian product). For each cell, the headline
state-level output (``by_state.csv``) is collated into one summary CSV
with one row per (cell × scenario × metric). A wide param-range CSV is
also emitted so :file:`poverty_impact_report.py --merge-sweep` can splice
``*_param_min`` / ``*_param_max`` columns into the headline report.

Sweep dimensions (defaults — override with kwargs)
--------------------------------------------------

  hi_ctc_takeup_rate            empirical band from HI EITC observed
                                take-up (see --hi-ctc-takeup-anchor;
                                falls back to 0.60 / 0.80 / 0.95 with
                                ``--hi-ctc-takeup-anchor literature``)
  hi_eitc_100pct_takeup_rate    0.85 / 0.95 / 1.00
  arpa_ctc_takeup_rate          0.85 / 0.95 / 0.98
  eitc_poverty_alpha            0.3  / 0.5  / 0.7

  Per-child HI CTC dollar amount is encoded in the scenario name
  (``hi_ctc_300`` / ``hi_ctc_650`` / ``hi_ctc_1000``), NOT swept here.
  See METHODOLOGY.md §"Policy-choice axes vs. parameter uncertainty".

OAT mode (default): one parameter swept at a time, others held at MID.
  Cell count: 4 params × 3 cells = 12 (MID-of-all duplicated 4 times is
  deduplicated automatically → 9 unique cells per geography).
Factorial mode: 3^4 = 81 cells; intended for overnight runs.

Examples
--------
  # OAT sweep at TY 2024 on the bundled fixture
  python scripts/poverty_impact_sweep.py --tax-year 2024 \\
      --out reports/poverty_sweep_2024/

  # OAT + replicate-weight SE merged into each cell's by_state.csv
  python scripts/poverty_impact_sweep.py --tax-year 2024 \\
      --replicate-weights --n-replicates 80 \\
      --out reports/poverty_sweep_2024/

  # Full factorial (slow)
  python scripts/poverty_impact_sweep.py --tax-year 2024 --factorial \\
      --out reports/poverty_sweep_2024_factorial/
"""
from __future__ import annotations

import argparse
import itertools
import logging
import sys
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

# poverty_impact_report.main(argv) is reused per cell — keep the script's
# directory on sys.path so the import works when this script is invoked
# from anywhere.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

LOG = logging.getLogger("poverty_impact_sweep")


# ---------------------------------------------------------------------------
# Sweep parameter definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SweepParam:
    """One swept parameter: CLI flag name + LOW/MID/HIGH values + source note."""

    name: str             # short slug used in cell names and CSV columns
    cli_flag: str         # the --foo-bar passed through to poverty_impact_report.py
    low: float
    mid: float
    high: float
    source: str           # one-line empirical anchor for the band

    def values(self) -> dict[str, float]:
        return {"low": self.low, "mid": self.mid, "high": self.high}


DEFAULT_SWEEP_PARAMS: tuple[SweepParam, ...] = (
    SweepParam(
        name="hi_ctc_takeup",
        cli_flag="--hi-ctc-takeup-rate",
        low=0.65, mid=0.70, high=0.75,
        source=(
            "Hawaii-empirical anchor: federal-EITC take-up in HI 2022 "
            "= 84,010 admin claims / 120,535 PUMS-eligible filers ≈ 0.697. "
            "±5pp judgment band; see "
            "tax_modeler.calibration.hi_eitc_takeup_estimate for the "
            "SDR-bootstrap variant (requires replicate weights on the "
            "tax-unit frame, currently only on the SPM-aggregated frame)."
        ),
    ),
    SweepParam(
        name="hi_eitc_100pct_takeup",
        cli_flag="--hi-eitc-100pct-takeup-rate",
        low=0.95, mid=0.98, high=1.00,
        source=(
            "Conditional take-up given existing HI EITC claim: HI EITC "
            "auto-computes on Form N-15 as fixed percentage of federal "
            "(Act 209, 2023). Conditional rate is near-perfect; band "
            "captures rare elect-out cases. Composite friction (non-claimers "
            "of federal EITC) is upstream in hi_eitc_amount."
        ),
    ),
    SweepParam(
        name="arpa_ctc_takeup",
        cli_flag="--arpa-ctc-takeup-rate",
        low=0.92, mid=0.94, high=0.95,
        source=(
            "Karpman et al. Urban Institute (2022) + Treasury reports on "
            "actual ARPA monthly-CTC participation among eligible families: "
            "0.92-0.95 empirical band, midpoint 0.94."
        ),
    ),
    # NOTE: ``hi_ctc_per_child`` was removed from the sweep in 2026-Q2.
    # The per-child dollar amount is a policy-design counterfactual
    # (different bills, not behavioral uncertainty), so it now lives in
    # the scenario name (``hi_ctc_300``, ``hi_ctc_650``, ``hi_ctc_1000``)
    # rather than the parameter sweep. Each scenario carries its own SE
    # and parameter-range columns. See METHODOLOGY.md §"Policy-choice
    # axes vs. parameter uncertainty" for the rationale.
    SweepParam(
        name="eitc_poverty_alpha",
        cli_flag="--eitc-poverty-alpha",
        low=0.3, mid=0.5, high=0.7,
        source="Forward-projection elasticity band (default 0.5 ± 40%)",
    ),
)


# ---------------------------------------------------------------------------
# Cell enumeration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Cell:
    """One concrete combination of all swept parameters."""

    label: str
    values: dict[str, float] = field(default_factory=dict)  # param.name -> value
    varied_param: str | None = None  # OAT: which param differs from MID
    varied_level: str | None = None  # "low" | "mid" | "high"

    def cli_overrides(self, params: Iterable[SweepParam]) -> list[str]:
        """Render this cell as additional CLI flags to pass through."""
        flags: list[str] = []
        for p in params:
            v = self.values[p.name]
            if isinstance(v, float) and v.is_integer():
                # Pass integer-valued floats as plain ints for cleanliness in
                # the recorded argv (e.g. "--n-replicates 80" not "80.0").
                flags.extend([p.cli_flag, str(int(v))])
            else:
                flags.extend([p.cli_flag, str(v)])
        return flags


def _enumerate_oat(params: tuple[SweepParam, ...]) -> list[Cell]:
    """Generate the OAT cell list: MID-of-all, plus LOW/HIGH for each param."""
    mid_values = {p.name: p.mid for p in params}
    cells: list[Cell] = [
        Cell(label="all_mid", values=dict(mid_values),
             varied_param=None, varied_level="mid")
    ]
    for p in params:
        for level in ("low", "high"):
            vals = dict(mid_values)
            vals[p.name] = p.values()[level]
            cells.append(Cell(
                label=f"{p.name}_{level}", values=vals,
                varied_param=p.name, varied_level=level,
            ))
    return cells


def _enumerate_factorial(params: tuple[SweepParam, ...]) -> list[Cell]:
    """Full Cartesian product over LOW/MID/HIGH per parameter (3^n cells)."""
    levels = ("low", "mid", "high")
    cells: list[Cell] = []
    for combo in itertools.product(levels, repeat=len(params)):
        vals = {p.name: p.values()[level] for p, level in zip(params, combo, strict=False)}
        label = "__".join(f"{p.name}={level}" for p, level in zip(params, combo, strict=False))
        cells.append(Cell(label=label, values=vals))
    return cells


# ---------------------------------------------------------------------------
# Per-cell execution: invoke poverty_impact_report.main(argv=...)
# ---------------------------------------------------------------------------

def _build_cell_argv(
    *,
    cell: Cell,
    params: tuple[SweepParam, ...],
    base_argv: list[str],
    cell_out_dir: Path,
) -> list[str]:
    """Compose the argv list that drives poverty_impact_report.py for one cell."""
    argv = list(base_argv)
    argv.extend(["--out", str(cell_out_dir)])
    argv.extend(cell.cli_overrides(params))
    return argv


def _run_cell(
    *,
    cell: Cell,
    params: tuple[SweepParam, ...],
    base_argv: list[str],
    out_root: Path,
) -> pd.DataFrame | None:
    """Run one cell, return its by_state.csv as a DataFrame (or None on error)."""
    # Import the report's main() lazily to avoid heavy import-time cost when
    # the sweep is just enumerated (e.g., during --dry-run).
    from poverty_impact_report import main as report_main

    cell_dir = out_root / "cells" / cell.label
    cell_dir.mkdir(parents=True, exist_ok=True)
    argv = _build_cell_argv(
        cell=cell, params=params, base_argv=base_argv, cell_out_dir=cell_dir,
    )
    LOG.info("Cell %s: argv=%s", cell.label, " ".join(argv))
    t0 = time.perf_counter()
    rc = report_main(argv)
    elapsed = time.perf_counter() - t0
    if rc != 0:
        LOG.error("Cell %s failed (rc=%d)", cell.label, rc)
        return None
    by_state = pd.read_csv(cell_dir / "by_state.csv")
    LOG.info("Cell %s: %d state rows, %.1fs", cell.label, len(by_state), elapsed)
    return by_state


# ---------------------------------------------------------------------------
# Collation
# ---------------------------------------------------------------------------

def _long_summary(
    *,
    cells: list[Cell],
    by_state_per_cell: dict[str, pd.DataFrame],
    params: tuple[SweepParam, ...],
) -> pd.DataFrame:
    """One row per (cell × column). Useful for plotting / pivot exploration."""
    rows: list[dict] = []
    for cell in cells:
        bs = by_state_per_cell.get(cell.label)
        if bs is None or bs.empty:
            continue
        s = bs.iloc[0]
        for col, val in s.items():
            if not isinstance(val, (int, float, np.integer, np.floating)):
                continue
            if pd.isna(val):
                continue
            row = {
                "cell_label": cell.label,
                "varied_param": cell.varied_param or "",
                "varied_level": cell.varied_level or "",
                "column": col,
                "value": float(val),
            }
            for p in params:
                row[p.name] = cell.values[p.name]
            rows.append(row)
    return pd.DataFrame(rows)


def _wide_param_ranges(
    *,
    long_df: pd.DataFrame,
) -> pd.DataFrame:
    """One row per output column; emit *_param_min, *_param_max, *_param_median.

    The min/max/median are computed across *all* sweep cells (OAT or
    factorial); for OAT this is the natural envelope of the
    one-parameter-at-a-time excursions.
    """
    if long_df.empty:
        return pd.DataFrame()
    grouped = long_df.groupby("column")["value"]
    out = pd.DataFrame({
        "column": grouped.groups.keys(),
        "param_min": grouped.min().values,
        "param_max": grouped.max().values,
        "param_median": grouped.median().values,
        "n_cells": grouped.count().values,
    })
    out = out.sort_values("column").reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--tax-year", type=int, required=True,
                   help="Tax year passed through to poverty_impact_report.py.")
    p.add_argument("--out", type=Path, required=True,
                   help="Output directory (cells/<label>/ subdirs created).")
    p.add_argument("--pums-data-dir", type=Path, default=None,
                   help="Forwarded to poverty_impact_report.py.")
    p.add_argument("--use-fixture", action="store_true",
                   help="Forwarded to poverty_impact_report.py.")
    p.add_argument("--replicate-weights", action="store_true", default=False,
                   help="Forwarded to poverty_impact_report.py (per-cell SDR SE).")
    p.add_argument("--n-replicates", type=int, default=80,
                   help="Forwarded to poverty_impact_report.py.")
    p.add_argument("--scenarios", type=str, default=None,
                   help="Forwarded to poverty_impact_report.py.")
    p.add_argument("--apply-rxkids", action="store_true", default=False,
                   help="Forwarded to poverty_impact_report.py.")
    p.add_argument(
        "--factorial", action="store_true", default=False,
        help="Run the full 3^5 = 243-cell factorial instead of the OAT default.",
    )
    p.add_argument(
        "--oat", action="store_true", default=False,
        help="(Default mode.) Sweep one parameter at a time around the mid point.",
    )
    p.add_argument(
        "--dry-run", action="store_true", default=False,
        help="Enumerate cells and print argv per cell; do not execute.",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _base_argv(args: argparse.Namespace) -> list[str]:
    """Construct the common (cell-invariant) argv pass-through."""
    argv = ["--tax-year", str(args.tax_year)]
    if args.pums_data_dir is not None:
        argv.extend(["--pums-data-dir", str(args.pums_data_dir)])
    if args.use_fixture:
        argv.append("--use-fixture")
    if args.replicate_weights:
        argv.extend(["--replicate-weights", "--n-replicates", str(args.n_replicates)])
    if args.scenarios:
        argv.extend(["--scenarios", args.scenarios])
    if args.apply_rxkids:
        argv.append("--apply-rxkids")
    if args.verbose:
        argv.append("-v")
    return argv


def main(argv: list | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.factorial and args.oat:
        LOG.error("--factorial and --oat are mutually exclusive.")
        return 2

    args.out.mkdir(parents=True, exist_ok=True)

    params = DEFAULT_SWEEP_PARAMS
    cells = (
        _enumerate_factorial(params) if args.factorial
        else _enumerate_oat(params)
    )
    LOG.info(
        "Sweep mode: %s, %d cells, %d params",
        "factorial" if args.factorial else "OAT",
        len(cells), len(params),
    )

    base_argv = _base_argv(args)

    if args.dry_run:
        for cell in cells:
            print(cell.label, "→", " ".join(cell.cli_overrides(params)))
        return 0

    by_state_per_cell: dict[str, pd.DataFrame] = {}
    t0 = time.perf_counter()
    for i, cell in enumerate(cells, start=1):
        LOG.info("=" * 70)
        LOG.info("[%d/%d] Cell %s", i, len(cells), cell.label)
        bs = _run_cell(
            cell=cell, params=params, base_argv=base_argv, out_root=args.out,
        )
        if bs is None:
            LOG.warning("Cell %s skipped due to error", cell.label)
            continue
        by_state_per_cell[cell.label] = bs
    elapsed = time.perf_counter() - t0
    LOG.info("All %d cells completed in %.1fs (%.1f min)",
             len(by_state_per_cell), elapsed, elapsed / 60.0)

    long_df = _long_summary(
        cells=cells, by_state_per_cell=by_state_per_cell, params=params,
    )
    wide_df = _wide_param_ranges(long_df=long_df)

    long_path = args.out / "summary.csv"
    wide_path = args.out / "param_ranges.csv"
    long_df.to_csv(long_path, index=False)
    wide_df.to_csv(wide_path, index=False)
    LOG.info("Wrote long summary  : %s (%d rows)", long_path, len(long_df))
    LOG.info("Wrote param ranges  : %s (%d cols)", wide_path, len(wide_df))

    if not wide_df.empty:
        # Eyeball summary: range relative to mid for headline persons_lifted cols.
        print("\nPersons-lifted ranges across sweep cells (state-level):")
        print("-" * 64)
        for _, row in wide_df.iterrows():
            col = row["column"]
            if not col.startswith("persons_lifted_") or col.endswith("_hoh") or col.endswith("_se"):
                continue
            print(
                f"  {col:<40} : min={row['param_min']:>12,.0f}"
                f"  max={row['param_max']:>12,.0f}"
                f"  median={row['param_median']:>12,.0f}"
            )
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
