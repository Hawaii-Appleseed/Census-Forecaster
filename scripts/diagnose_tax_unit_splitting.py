#!/usr/bin/env python3
"""Diagnose tax-unit over-splitting and childless-EITC-claimer inflation.

Read-only. Builds tax units (no SOI calibration), enriches, computes base
tax (federal EITC), and reports:

  1. tax_units_per_household  — mean / median / max / P95 / histogram
  2. childless EITC claimer share (weighted + unweighted) — among units with
     eitc_amount > 0, the share with num_qualifying_children == 0
  3. childless single-adult units per household (the over-split signal)

Baseline (pre-fix): ~1.71 units/HH, max 13, ~47% childless EITC claimers.
IRS national benchmark: ~21% childless.

Usage
-----
    uv run python scripts/diagnose_tax_unit_splitting.py --source fixture
    uv run python scripts/diagnose_tax_unit_splitting.py --source pums \\
        --pums-dir packages/data/raw/pums_2024_1yr
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "tax_modeler" / "fixtures"
DEFAULT_PUMS_DIR = REPO_ROOT / "packages" / "data" / "raw" / "pums_2024_1yr"


def _load_pums(source: str, pums_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    if source == "fixture":
        persons = pd.read_parquet(FIXTURE_DIR / "synthetic_pums_persons.parquet")
        households = pd.read_parquet(FIXTURE_DIR / "synthetic_pums_households.parquet")
        return persons, households

    for ext in ("parquet", "csv"):
        p = pums_dir / f"psam_p15.{ext}"
        h = pums_dir / f"psam_h15.{ext}"
        if p.exists() and h.exists():
            reader = pd.read_parquet if ext == "parquet" else pd.read_csv
            return reader(p), reader(h)
    raise FileNotFoundError(
        f"No psam_p15/psam_h15 (.parquet/.csv) found in {pums_dir}"
    )


def _weight(df: pd.DataFrame) -> np.ndarray:
    for col in ("weight", "weight_calibrated", "WGTP"):
        if col in df.columns:
            return df[col].fillna(0).to_numpy(dtype=float)
    return np.ones(len(df), dtype=float)


def _build_units(
    persons: pd.DataFrame, households: pd.DataFrame, tax_year: int, apply_takeup: bool
) -> pd.DataFrame:
    from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
    from tax_modeler.units.constructor import TaxUnitConstructor

    ctor = TaxUnitConstructor(
        persons.copy(), households.copy(),
        use_soi_calibration=False, progress_bar=False,
    )
    units = ctor.create_rule_based_units(parallel=False)
    units = enrich_for_credits(units)
    units = compute_base_tax(units, tax_year=tax_year)
    if apply_takeup:
        # Match the production pipeline: rank-and-truncate EITC eligibles to the
        # IRS SOI Hawaii claim target (~84k). Small childless credits are
        # dropped first, so the post-takeup childless share is what the forecast
        # actually reports — a different metric than raw eligibility.
        from tax_modeler.pipeline import apply_credit_takeup
        units = apply_credit_takeup(units, year=2022, programs=("eitc",))
    return units


def _report(units: pd.DataFrame, n_households: int) -> None:
    n_units = len(units)
    per_hh = units.groupby("SERIALNO").size() if "SERIALNO" in units.columns else pd.Series(dtype=int)

    print("=" * 60)
    print("1. TAX UNITS PER HOUSEHOLD")
    print("=" * 60)
    print(f"  households (with >=1 unit): {len(per_hh):,}")
    print(f"  tax units total:            {n_units:,}")
    if len(per_hh):
        print(f"  mean:   {per_hh.mean():.3f}")
        print(f"  median: {per_hh.median():.1f}")
        print(f"  P95:    {per_hh.quantile(0.95):.1f}")
        print(f"  max:    {per_hh.max()}")
        hist = per_hh.value_counts().sort_index()
        print("  distribution (units : #households):")
        for k, v in hist.items():
            print(f"    {k:>3} : {v:,}")

    print()
    print("=" * 60)
    print("2. CHILDLESS EITC CLAIMER SHARE")
    print("=" * 60)
    if "eitc_amount" not in units.columns:
        print("  (eitc_amount missing — cannot compute)")
    else:
        w = _weight(units)
        claimers = units["eitc_amount"].fillna(0).to_numpy() > 0
        nqc = units.get("num_qualifying_children", pd.Series(0, index=units.index)).fillna(0).to_numpy()
        childless = claimers & (nqc == 0)

        n_claim = int(claimers.sum())
        n_childless = int(childless.sum())
        w_claim = float(w[claimers].sum())
        w_childless = float(w[childless].sum())

        print(f"  EITC claimers (unweighted):  {n_claim:,}")
        print(f"  childless (unweighted):      {n_childless:,}  "
              f"({100 * n_childless / n_claim:.1f}%)" if n_claim else "  no claimers")
        print(f"  EITC claimers (weighted):    {w_claim:,.0f}")
        if w_claim:
            print(f"  childless (weighted):        {w_childless:,.0f}  "
                  f"<<< {100 * w_childless / w_claim:.1f}% (IRS national ~21%)")

    print()
    print("=" * 60)
    print("3. CHILDLESS SINGLE-ADULT UNITS PER HOUSEHOLD")
    print("=" * 60)
    if "filing_status" in units.columns:
        nqc = units.get("num_qualifying_children", pd.Series(0, index=units.index)).fillna(0).to_numpy()
        single_childless = (units["filing_status"] == "single").to_numpy() & (nqc == 0)
        print(f"  childless single units: {int(single_childless.sum()):,}")
        print(f"  per household:          {single_childless.sum() / max(n_households, 1):.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", choices=["fixture", "pums"], default="fixture")
    ap.add_argument("--pums-dir", type=Path, default=DEFAULT_PUMS_DIR)
    ap.add_argument("--tax-year", type=int, default=2023)
    ap.add_argument(
        "--apply-takeup", action="store_true",
        help="Apply IRS-anchored EITC take-up (production-relevant childless share).",
    )
    args = ap.parse_args()

    persons, households = _load_pums(args.source, args.pums_dir)
    n_households = households["SERIALNO"].nunique() if "SERIALNO" in households.columns else len(households)
    print(f"Source: {args.source}  |  persons: {len(persons):,}  households: {n_households:,}  "
          f"tax_year: {args.tax_year}\n")

    units = _build_units(persons, households, args.tax_year, args.apply_takeup)
    if args.apply_takeup:
        print("(post-IRS-take-up: EITC truncated to SOI Hawaii claim target)\n")
    _report(units, n_households)


if __name__ == "__main__":
    main()
