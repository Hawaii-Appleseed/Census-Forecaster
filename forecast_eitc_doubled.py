"""Federal + Hawaii state EITC expansion: bottom-quintile poverty impact.

Models a "doubled EITC" reform combining:

  * Federal EITC × 2 (``benefit_overrides={"eitc": {"amount_pct": 2.0}}``)
  * HI state EITC rate from 40% to 80% of federal
    (``benefit_overrides={"hi_eitc": {"rate_of_federal": 0.80}}``)

This pair is the most consequential refundable-credit reform on the
table for working-poor Hawaii households. The output highlights:

  * Combined federal + state EITC dollar flow change
  * Bottom-quintile per-filer income change
  * SPM poverty rate before / after

Output:
  /tmp/forecast_eitc_doubled.csv  — quintile breakdown
  Stdout                          — headline summary

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


def _load_baseline_units() -> pd.DataFrame:
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
    parser.add_argument("--federal-multiplier", type=float, default=2.0,
                        help="Multiplier on federal EITC (default 2.0)")
    parser.add_argument("--hi-rate", type=float, default=0.80,
                        help="HI state EITC rate of federal (default 0.80; current law 0.40)")
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument("--out", type=Path,
                        default=Path("/tmp/forecast_eitc_doubled.csv"))
    parser.add_argument("--by-puma", action="store_true",
                        help="Also report poverty + EITC flow by PUMA")
    parser.add_argument("--puma-min-count", type=float, default=100.0)
    args = parser.parse_args(argv)

    print("Loading baseline units + computing all benefits...", flush=True)
    units = _load_baseline_units()
    print(f"  {len(units):,} units; baseline federal EITC "
          f"${(units['eitc_amount']*units['weight']).sum()/1e6:.2f}M, "
          f"HI EITC ${(units['hi_eitc_amount']*units['weight']).sum()/1e6:.2f}M",
          flush=True)

    reform = Reform(
        name=f"eitc_x{args.federal_multiplier}_hi_x{args.hi_rate / 0.40:.1f}",
        benefit_overrides={
            "eitc": {"amount_pct": args.federal_multiplier},
            "hi_eitc": {"rate_of_federal": args.hi_rate},
        },
        metadata={
            "description": (
                f"Federal EITC × {args.federal_multiplier}; "
                f"HI EITC rate {args.hi_rate * 100:.0f}% of federal"
            )
        },
    )
    print(f"\nApplying reform: {reform.name}", flush=True)
    result = apply_reform(units, reform, year=args.year)
    cf = result.counterfactual_units

    deltas_m = result.benefit_flow_deltas_millions
    print("\nCredit flow changes (M$/year):", flush=True)
    for prog in ("eitc", "hi_eitc"):
        print(f"  {prog:<12s} {deltas_m.get(prog, 0.0):+.2f}", flush=True)

    base_resources, _ = compute_spm_resources(units)
    cf_resources, _ = compute_spm_resources(cf)
    threshold = hawaii_spm_threshold(2024)
    base_rate = poverty_rate(
        base_resources["spm_resources"], threshold, base_resources["weight"]
    )
    cf_rate = poverty_rate(
        cf_resources["spm_resources"], threshold, cf_resources["weight"]
    )
    print(f"\nSPM poverty rate baseline: {base_rate * 100:.2f}%", flush=True)
    print(f"SPM poverty rate post:     {cf_rate * 100:.2f}%", flush=True)
    print(f"Change:                    {(cf_rate - base_rate) * 100:+.2f} pp", flush=True)

    # Quintile breakdown
    quintile = weighted_ntile_labels(units["income"], units["weight"], n=5, label_prefix="Q")
    rows = []
    for q in [f"Q{i}" for i in range(1, 6)]:
        mask = (quintile == q).to_numpy()
        if not mask.any():
            continue
        w = units.loc[mask, "weight"].to_numpy()
        base_eitc_m = float((units.loc[mask, "eitc_amount"].to_numpy() * w).sum() / 1e6)
        cf_eitc_m = float((cf.loc[mask, "eitc_amount"].to_numpy() * w).sum() / 1e6)
        base_hi_m = float((units.loc[mask, "hi_eitc_amount"].to_numpy() * w).sum() / 1e6)
        cf_hi_m = float((cf.loc[mask, "hi_eitc_amount"].to_numpy() * w).sum() / 1e6)
        rows.append({
            "quintile": q,
            "n_filers_weighted": float(w.sum()),
            "fed_eitc_baseline_$M": round(base_eitc_m, 3),
            "fed_eitc_post_$M": round(cf_eitc_m, 3),
            "hi_eitc_baseline_$M": round(base_hi_m, 3),
            "hi_eitc_post_$M": round(cf_hi_m, 3),
            "combined_delta_$M": round((cf_eitc_m - base_eitc_m) + (cf_hi_m - base_hi_m), 3),
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
        ).rename(columns={"poverty_rate": "poverty_rate_baseline"})
        thr_cf = threshold_for_units(cf_resources, year=2024)
        cf_geo = poverty_by_geography(
            cf_resources, resources_col="spm_resources",
            threshold=thr_cf, geo_col="PUMA",
        ).rename(columns={"poverty_rate": "poverty_rate_post"})
        joined = base_geo[["count_weighted", "poverty_rate_baseline"]].join(
            cf_geo[["poverty_rate_post"]]
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
