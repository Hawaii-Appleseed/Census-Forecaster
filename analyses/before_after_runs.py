#!/usr/bin/env python3
"""Before/after of every published run file: BEFORE = a runs directory (main's, as published), AFTER = another.

Every cell is written (unchanged ones have abs_change 0). Compares each CSV (rows matched on their non-numeric columns and year columns) and each JSON (flattened to key
paths) cell by cell and writes a long table: file, row key, column, before, after, abs change, % change.

    uv run python analyses/before_after_runs.py --before runs --after /tmp/scratch_runs --out out.csv
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd

FILES = [
    "sb3125_cd2_enhanced/enhanced.csv", "sb3125_cd2_enhanced/quintile.csv", "sb3125_cd2_enhanced/bracket.csv",
    "act24_vs_pre_act46/decomposition.csv",
    "sb3125_cd2_fy26base/*.csv",
    "cg_rate_options/revenue_by_year.csv", "cg_rate_options/distribution_ty2027_act24.csv",
    "cg_rate_options/distribution_ty2026_act46.csv", "cg_rate_options/nonresident_addon.csv",
    "cg_rate_options/anchor_check.csv",
    "working_family_credits/*.csv",
    "hr1_snap_medicaid/*.csv", "hr1_snap_medicaid/summary.json",
    "targeting_efficiency/*.csv", "targeting_efficiency_one_year/*.csv",
    "tax_simulator/cg_page_comparison.json", "tax_simulator/credits.json", "tax_simulator/web/presets_results.json",
]
KEY_NUMERIC = {"tax_year", "year", "ty", "quintile", "scenario_year"}


def flatten(obj, prefix=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from flatten(v, f"{prefix}{k}.")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from flatten(v, f"{prefix}{i}.")
    else:
        yield prefix.rstrip("."), obj


def _fmt(v) -> str:
    return str(int(v)) if isinstance(v, float) and v == v and v == int(v) else str(v)


def cells_csv(path: Path) -> dict[tuple[str, str], object]:
    df = pd.read_csv(path)
    keycols = [c for c in df.columns if not pd.api.types.is_numeric_dtype(df[c]) or c in KEY_NUMERIC]
    out: dict[tuple[str, str], object] = {}
    seen: dict[str, int] = {}
    for i, row in df.iterrows():
        key = "|".join(_fmt(row[c]) for c in keycols) if keycols else f"row{i}"
        n = seen.get(key, 0)
        seen[key] = n + 1
        key = key if n == 0 else f"{key}#{n}"
        for c in df.columns:
            if c in keycols:
                continue
            out[(key, c)] = row[c]
    return out


def cells_json(path: Path) -> dict[tuple[str, str], object]:
    return {("", k): v for k, v in flatten(json.loads(path.read_text()))}


def expand(root: Path) -> list[str]:
    names: list[str] = []
    for pat in FILES:
        names += sorted(str(p.relative_to(root)) for p in root.glob(pat))
    return names


def compare(before: Path, after: Path) -> pd.DataFrame:
    rows = []
    for rel in sorted(set(expand(before)) | set(expand(after))):
        b, a = before / rel, after / rel
        if not b.exists() or not a.exists():
            rows.append({"file": rel, "key": "", "column": "(file)", "before": "present" if b.exists() else "missing",
                         "after": "present" if a.exists() else "missing", "abs_change": np.nan, "pct_change": np.nan})
            continue
        fn = cells_json if rel.endswith(".json") else cells_csv
        cb, ca = fn(b), fn(a)
        for k in cb.keys() | ca.keys():
            vb, va = cb.get(k), ca.get(k)
            try:
                fb, fa = float(vb), float(va)
                if np.isnan(fb) and np.isnan(fa):
                    continue
                ab = fa - fb
                pct = ab / abs(fb) * 100 if fb not in (0.0,) else np.nan
            except (TypeError, ValueError):
                if str(vb) == str(va):
                    continue
                ab = pct = np.nan
            rows.append({"file": rel, "key": k[0], "column": k[1], "before": vb, "after": va,
                         "abs_change": ab, "pct_change": pct})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--before", type=Path, required=True)
    ap.add_argument("--after", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    d = compare(args.before, args.after)
    d.to_csv(args.out, index=False)
    changed = d[(d.abs_change.abs() > 0) | d.abs_change.isna()]
    print(f"{len(d):,} cells compared, {len(changed):,} changed, in {d.file.nunique()} files -> {args.out}")


if __name__ == "__main__":
    main()
