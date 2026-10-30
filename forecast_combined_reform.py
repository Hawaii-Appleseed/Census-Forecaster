"""Combined tax + benefit reform impact (Hawaii).

Demonstrates that the Reform DSL handles tax-rate AND benefit changes
in a single ``Reform``. Default scenario: SB 3125 CD1 brackets (a tax
cut on top earners) paired with a 5% SNAP cut and a doubling of the
HI state EITC rate.

This script answers: *if Hawaii cuts top-bracket taxes, expands HI
EITC, and tightens SNAP, what is the net distributional effect?*

Output:
  /tmp/forecast_combined_reform.csv  — quintile-level dollar flows
  Stdout                             — headline summary

Requires the workspace to be installed: ``uv sync --all-packages``.
"""
from __future__ import annotations

import argparse
import logging
import sys
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.WARNING)

import pandas as pd

from tax_modeler import (
    Reform,
    TaxSystemRegistry,
    apply_reform,
    compute_base_tax,
    compute_hi_eitc_for_units,
    compute_hi_food_excise_for_units,
    compute_hi_renters_for_units,
    compute_snap_for_units,
    compute_spm_resources,
    compute_ssi_for_units,
    compute_ssi_hi_supplement_for_units,
    enrich_for_credits,
    hawaii_spm_threshold,
    poverty_rate,
    weighted_ntile_labels,
)


def _baseline_units() -> pd.DataFrame:
    fixture_dir = (
        Path(__file__).resolve().parent
        / "tests" / "tax_modeler" / "fixtures"
    )
    persons = pd.read_parquet(fixture_dir / "synthetic_pums_persons.parquet")
    households = pd.read_parquet(fixture_dir / "synthetic_pums_households.parquet")
    from tax_modeler.base import build_raw_units
    units = build_raw_units(persons.copy(), households.copy())
    units = enrich_for_credits(units)
    units = compute_base_tax(units)
    units = compute_snap_for_units(units)
    units = compute_ssi_for_units(units)
    units = compute_ssi_hi_supplement_for_units(units)
    units = compute_hi_eitc_for_units(units)
    units = compute_hi_food_excise_for_units(units)
    units = compute_hi_renters_for_units(units)
    return units


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int, default=2027,
                        help="Tax year (SB 3125 CD1 takes effect TY2027)")
    parser.add_argument("--snap-cut-pct", type=float, default=0.05,
                        help="SNAP cut fraction (default 0.05 = 5%%)")
    parser.add_argument("--hi-eitc-rate", type=float, default=0.80,
                        help="HI state EITC rate of federal (default 0.80)")
    parser.add_argument("--out", type=Path,
                        default=Path("/tmp/forecast_combined_reform.csv"))
    parser.add_argument("--by-puma", action="store_true",
                        help="Also report poverty + SPM resources by PUMA")
    parser.add_argument("--puma-min-count", type=float, default=100.0)
    args = parser.parse_args(argv)

    print("Building baseline units + benefits...", flush=True)
    units = _baseline_units()
    n = len(units)
    print(f"  {n:,} units, {(units['weight']).sum():,.0f} weighted filers", flush=True)

    reform = Reform(
        name="combined_reform",
        tax_system_factory=TaxSystemRegistry.get_sb3125_cd1_system,
        benefit_overrides={
            "snap": {"max_benefit_pct": 1.0 - args.snap_cut_pct},
            "hi_eitc": {"rate_of_federal": args.hi_eitc_rate},
        },
        metadata={
            "description": (
                "SB 3125 CD1 brackets + "
                f"{int(args.snap_cut_pct * 100)}% SNAP cut + "
                f"HI EITC rate {int(args.hi_eitc_rate * 100)}% of federal"
            ),
        },
    )

    print(f"\nApplying reform: {reform.name}", flush=True)
    print(f"  description: {reform.metadata['description']}", flush=True)
    result = apply_reform(units, reform, year=args.year)
    cf = result.counterfactual_units

    print("\nFiscal flow changes (M$/year):", flush=True)
    print(f"  Tax revenue:           {result.revenue_delta_millions:+.2f}", flush=True)
    for prog, delta in sorted(result.benefit_flow_deltas_millions.items()):
        print(f"  {prog:<22s} {delta:+.2f}", flush=True)

    # Net fiscal impact (state = revenue change minus state-credit flow)
    snap_delta = result.benefit_flow_deltas_millions.get("snap", 0.0)
    hi_eitc_delta = result.benefit_flow_deltas_millions.get("hi_eitc", 0.0)
    net_state = result.revenue_delta_millions - hi_eitc_delta
    print(f"\nNet state cost (revenue change − state-credit change): {net_state:+.2f} M$", flush=True)

    # SPM rate / quintile distributional table
    base_resources, _ = compute_spm_resources(units)
    cf_resources, _ = compute_spm_resources(cf)
    threshold = hawaii_spm_threshold(2024)
    base_rate = poverty_rate(
        base_resources["spm_resources"], threshold, base_resources["weight"]
    )
    cf_rate = poverty_rate(
        cf_resources["spm_resources"], threshold, cf_resources["weight"]
    )
    print(f"\nSPM poverty rate: {base_rate * 100:.2f}% → {cf_rate * 100:.2f}% "
          f"({(cf_rate - base_rate) * 100:+.2f} pp)", flush=True)

    quintile = weighted_ntile_labels(units["income"], units["weight"], n=5, label_prefix="Q")
    rows = []
    for q in [f"Q{i}" for i in range(1, 6)]:
        mask = (quintile == q).to_numpy()
        if not mask.any():
            continue
        w = units.loc[mask, "weight"].to_numpy()
        base_spm = base_resources.loc[mask, "spm_resources"].to_numpy()
        cf_spm = cf_resources.loc[mask, "spm_resources"].to_numpy()
        rows.append({
            "quintile": q,
            "weighted_filers": float(w.sum()),
            "spm_baseline_mean_$": round(float((base_spm * w).sum() / w.sum()), 0),
            "spm_post_mean_$": round(float((cf_spm * w).sum() / w.sum()), 0),
            "spm_mean_delta_$": round(float(((cf_spm - base_spm) * w).sum() / w.sum()), 0),
            "spm_total_delta_$M": round(float(((cf_spm - base_spm) * w).sum() / 1e6), 3),
        })
    out = pd.DataFrame(rows)
    out.to_csv(args.out, index=False)
    print(f"\nQuintile breakdown saved: {args.out}", flush=True)
    print(out.to_string(index=False), flush=True)

    if args.by_puma:
        from tax_modeler import poverty_by_geography, threshold_for_units
        puma_out = args.out.with_name(args.out.stem + "_by_puma.csv")
        thr_base = threshold_for_units(base_resources, year=2024)
        base_geo = poverty_by_geography(
            base_resources, resources_col="spm_resources",
            threshold=thr_base, geo_col="PUMA",
        ).rename(columns={
            "poverty_rate": "poverty_rate_baseline",
            "mean_resources_$": "mean_resources_baseline_$",
        })
        thr_cf = threshold_for_units(cf_resources, year=2024)
        cf_geo = poverty_by_geography(
            cf_resources, resources_col="spm_resources",
            threshold=thr_cf, geo_col="PUMA",
        ).rename(columns={
            "poverty_rate": "poverty_rate_post",
            "mean_resources_$": "mean_resources_post_$",
        })
        joined = base_geo[["count_weighted", "poverty_rate_baseline",
                           "mean_resources_baseline_$"]].join(
            cf_geo[["poverty_rate_post", "mean_resources_post_$"]]
        )
        joined["poverty_rate_delta_pp"] = (
            joined["poverty_rate_post"] - joined["poverty_rate_baseline"]
        ) * 100
        joined.index = [
            f"~{p}" if c < args.puma_min_count else str(p)
            for p, c in zip(joined.index, joined["count_weighted"])
        ]
        joined.to_csv(puma_out)
        print(f"\nPer-PUMA breakdown saved: {puma_out}", flush=True)
        print(f"  (PUMAs with ~prefix have weighted count < {args.puma_min_count:.0f}; "
              "interpret cautiously)", flush=True)
        print(joined.round(4).to_string(), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
