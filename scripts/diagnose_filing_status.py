#!/usr/bin/env python3
"""Diagnose the model's filing-status mix vs DOTAX Hawaii benchmarks.

Read-only. Builds tax units the way the production poverty/EITC path does
(``use_soi_calibration=False`` — natural PUMS weights, no IPF rake), enriches,
computes base tax, and reports the weighted filing-status distribution against
the DOTAX TY2022 targets the IPF rake / ``calibrate_to_soi_totals`` reconcile
against.

Three views:
  1. AGGREGATE — weighted share of every tax unit by status vs DOTAX shares.
     (Model counts non-filing units too, so raw counts run above DOTAX returns;
     compare *shares*.)
  2. MARRIED units (MFJ+MFS) — the MFJ-vs-MFS split, vs DOTAX 93.1% / 6.9%.
  3. WITH-CHILDREN units — MFJ vs HoH vs single among units with qualifying
     children, the subset where MFJ was suspected under-assigned. PUMS-native
     married share among HI families with own kids is ~66% (per prior analysis).

Usage
-----
    uv run python scripts/diagnose_filing_status.py --source pums --tax-year 2022
    uv run python scripts/diagnose_filing_status.py --source fixture
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = REPO_ROOT / "tests" / "tax_modeler" / "fixtures"
DEFAULT_PUMS_DIR = REPO_ROOT / "packages" / "data" / "raw" / "pums_2024_1yr"

# DOTAX Hawaii filing-status benchmarks (TY2022) — the same targets the IPF rake
# (calibration/ipf_orchestrator.py) and units/status/irs_based.calibrate_to_soi_totals
# reconcile against. Total 618,423 returns.
DOTAX_FS_COUNTS = {
    "single": 326_470,
    "married_filing_jointly": 210_724,
    "head_of_household": 65_638,
    "married_filing_separately": 15_591,
}
STATUSES = list(DOTAX_FS_COUNTS)
_LABEL = {
    "single": "single",
    "married_filing_jointly": "MFJ",
    "head_of_household": "HoH",
    "married_filing_separately": "MFS",
}


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
    raise FileNotFoundError(f"No psam_p15/psam_h15 (.parquet/.csv) found in {pums_dir}")


def _weight(df: pd.DataFrame) -> np.ndarray:
    for col in ("weight", "weight_calibrated", "WGTP"):
        if col in df.columns:
            return df[col].fillna(0).to_numpy(dtype=float)
    return np.ones(len(df), dtype=float)


def _build_units(persons: pd.DataFrame, households: pd.DataFrame, tax_year: int) -> pd.DataFrame:
    from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
    from tax_modeler.units.constructor import TaxUnitConstructor

    ctor = TaxUnitConstructor(
        persons.copy(), households.copy(),
        use_soi_calibration=False, progress_bar=False,
    )
    units = ctor.create_rule_based_units(parallel=False)
    units = enrich_for_credits(units)
    units = compute_base_tax(units, tax_year=tax_year)
    return units


def _weighted_by_status(units: pd.DataFrame, mask: np.ndarray | None = None) -> dict[str, float]:
    w = _weight(units)
    if mask is not None:
        w = np.where(mask, w, 0.0)
    fs = units["filing_status"].astype(str).to_numpy()
    return {s: float(w[fs == s].sum()) for s in STATUSES}


def _print_share_table(title: str, counts: dict[str, float], targets: dict[str, float] | None) -> None:
    total = sum(counts.values())
    print("=" * 68)
    print(title)
    print("=" * 68)
    if total <= 0:
        print("  (no weighted units)")
        return
    tgt_total = sum(targets.values()) if targets else None
    print(f"  {'status':<8} {'weighted':>12} {'model %':>9}"
          + (f" {'DOTAX %':>9} {'gap':>8}" if targets else ""))
    print("  " + "-" * 64)
    for s in STATUSES:
        c = counts.get(s, 0.0)
        share = c / total
        line = f"  {_LABEL[s]:<8} {c:>12,.0f} {share:>8.1%}"
        if targets:
            tshare = targets[s] / tgt_total
            line += f" {tshare:>8.1%} {share - tshare:>+7.1%}"
        print(line)
    print("  " + "-" * 64)
    print(f"  {'total':<8} {total:>12,.0f}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--source", choices=["fixture", "pums"], default="pums")
    ap.add_argument("--pums-dir", type=Path, default=DEFAULT_PUMS_DIR)
    ap.add_argument("--tax-year", type=int, default=2022)
    args = ap.parse_args()

    persons, households = _load_pums(args.source, args.pums_dir)
    n_hh = households["SERIALNO"].nunique() if "SERIALNO" in households.columns else len(households)
    print(f"Source: {args.source}  |  persons: {len(persons):,}  households: {n_hh:,}  "
          f"tax_year: {args.tax_year}\n")

    units = _build_units(persons, households, args.tax_year)

    # 1. Aggregate distribution vs DOTAX.
    agg = _weighted_by_status(units)
    _print_share_table("1. AGGREGATE filing-status mix vs DOTAX TY2022", agg, DOTAX_FS_COUNTS)

    # 2. MFJ-vs-MFS split among married units.
    print()
    married_model = agg["married_filing_jointly"] + agg["married_filing_separately"]
    married_dotax = DOTAX_FS_COUNTS["married_filing_jointly"] + DOTAX_FS_COUNTS["married_filing_separately"]
    print("=" * 68)
    print("2. MFJ vs MFS within MARRIED units")
    print("=" * 68)
    if married_model > 0:
        mfj_share = agg["married_filing_jointly"] / married_model
        print(f"  model  : MFJ {mfj_share:>6.1%} / MFS {1 - mfj_share:>6.1%}  "
              f"(married weighted {married_model:,.0f})")
    print(f"  DOTAX  : MFJ {DOTAX_FS_COUNTS['married_filing_jointly'] / married_dotax:>6.1%} / "
          f"MFS {DOTAX_FS_COUNTS['married_filing_separately'] / married_dotax:>6.1%}  "
          f"(married returns {married_dotax:,})")

    # 3. With-children subset.
    print()
    nqc = units.get("num_qualifying_children", pd.Series(0, index=units.index)).fillna(0).to_numpy()
    has_kids = nqc > 0
    kids = _weighted_by_status(units, has_kids)
    _print_share_table(
        "3. WITH-CHILDREN units (num_qualifying_children > 0)", kids, None
    )
    kids_total = sum(kids.values())
    if kids_total > 0:
        mfj_kids = kids["married_filing_jointly"] / kids_total
        print(f"  --> MFJ share among with-children units: {mfj_kids:.1%} "
              f"(PUMS-native married-with-kids ~66%)")


if __name__ == "__main__":
    main()
