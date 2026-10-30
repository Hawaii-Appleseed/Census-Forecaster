"""SNAP-cut fiscal and poverty impact for Hawaii.

Demonstrates the Phase 1-5 policy-impact stack:

  1. Build calibrated tax units (or load from cache)
  2. Compute baseline benefits (SNAP, SSI, HI state EITC, etc.)
  3. Calibrate baseline take-up against HI DHS administrative caseload
  4. Apply a Reform with ``benefit_overrides={"snap": {"max_benefit_pct": 0.90}}``
  5. Compute SPM resources baseline vs counterfactual
  6. Report SNAP dollar flow, poverty-rate change, decile distribution

Output:
  /tmp/forecast_snap_10pct_cut.csv  — per-decile income / SPM / poverty deltas
  Stdout                            — headline summary

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
    AdminCaseload,
    Reform,
    apply_reform,
    calibrate_benefits,
    compute_hi_eitc_for_units,
    compute_hi_food_excise_for_units,
    compute_hi_renters_for_units,
    compute_snap_for_units,
    compute_spm_resources,
    compute_ssi_for_units,
    compute_ssi_hi_supplement_for_units,
    decile_summary,
    enrich_for_credits,
    compute_base_tax,
    hawaii_spm_threshold,
    poverty_rate,
)


def _load_synthetic_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load the bundled 50-household synthetic PUMS for demonstration runs."""
    fixture_dir = (
        Path(__file__).resolve().parent
        / "tests" / "tax_modeler" / "fixtures"
    )
    persons = pd.read_parquet(fixture_dir / "synthetic_pums_persons.parquet")
    households = pd.read_parquet(fixture_dir / "synthetic_pums_households.parquet")
    return persons, households


def _build_baseline_units(persons: pd.DataFrame, households: pd.DataFrame) -> pd.DataFrame:
    """Construct calibrated tax units from PUMS-style person + household frames."""
    from tax_modeler.base import build_raw_units

    print("Constructing tax units...", flush=True)
    units = build_raw_units(persons.copy(), households.copy())
    units = enrich_for_credits(units)
    units = compute_base_tax(units)
    print(f"  {len(units):,} tax units; gross HI tax ${(units['hi_tax_liability']*units['weight']).sum()/1e6:.1f}M",
          flush=True)
    return units


def _populate_all_benefits(units: pd.DataFrame) -> pd.DataFrame:
    """Run every benefit / state-credit module in baseline mode."""
    df = compute_snap_for_units(units)
    df = compute_ssi_for_units(df)
    df = compute_ssi_hi_supplement_for_units(df)
    df = compute_hi_eitc_for_units(df)
    df = compute_hi_food_excise_for_units(df)
    df = compute_hi_renters_for_units(df)
    return df


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cut-pct", type=float, default=0.10,
                        help="Fractional SNAP cut (default 0.10 = 10%%)")
    parser.add_argument("--year", type=int, default=2026,
                        help="Tax year for the reform comparison")
    parser.add_argument("--no-takeup-imputation", action="store_true",
                        help="Skip TRIM3-style baseline calibration to admin caseload")
    parser.add_argument("--out", type=Path,
                        default=Path("/tmp/forecast_snap_10pct_cut.csv"))
    parser.add_argument("--by-puma", action="store_true",
                        help="Also report poverty + income deltas by PUMA")
    parser.add_argument("--puma-min-count", type=float, default=100.0,
                        help="Weighted-count threshold below which PUMAs are flagged "
                             "(`~` prefix) due to small-sample instability")
    parser.add_argument("--reform-yaml", type=Path, default=None,
                        help="Path to a Reform YAML spec; overrides --cut-pct when set")
    args = parser.parse_args(argv)

    persons, households = _load_synthetic_fixture()
    units = _build_baseline_units(persons, households)
    units = _populate_all_benefits(units)

    if not args.no_takeup_imputation:
        try:
            caseload = AdminCaseload.load()
            units = calibrate_benefits(units, caseload=caseload, year=2024)
            print("  Take-up imputation applied (baseline calibrated to HI DHS caseload)",
                  flush=True)
        except Exception as e:
            print(f"  WARNING: take-up imputation skipped: {e}", flush=True)

    # Apply the SNAP-cut reform — from YAML when supplied, else built inline
    if args.reform_yaml:
        reform = Reform.from_yaml(args.reform_yaml)
        print(f"\nLoaded reform from {args.reform_yaml}", flush=True)
    else:
        reform = Reform(
            name=f"snap_minus_{int(args.cut_pct * 100)}pct",
            benefit_overrides={"snap": {"max_benefit_pct": 1.0 - args.cut_pct}},
        )
    print(f"Applying reform: {reform.name}", flush=True)
    result = apply_reform(units, reform, year=args.year)
    cf = result.counterfactual_units

    # Compute SPM resources for both worlds
    base_resources, _ = compute_spm_resources(units)
    cf_resources, _ = compute_spm_resources(cf)
    threshold = hawaii_spm_threshold(2024)

    base_rate = poverty_rate(
        base_resources["spm_resources"], threshold, base_resources["weight"]
    )
    cf_rate = poverty_rate(
        cf_resources["spm_resources"], threshold, cf_resources["weight"]
    )

    snap_delta_m = result.benefit_flow_deltas_millions.get("snap", 0.0)

    # Headline summary
    print("\n" + "=" * 70, flush=True)
    print(f"SNAP {-args.cut_pct:+.0%} CUT — fiscal & poverty impact, TY {args.year}", flush=True)
    print("=" * 70, flush=True)
    print(f"SNAP outlay change:        {snap_delta_m:+.2f} M$/year", flush=True)
    print(f"SPM poverty rate baseline: {base_rate * 100:.2f}%", flush=True)
    print(f"SPM poverty rate w/ cut:   {cf_rate * 100:.2f}%", flush=True)
    print(f"Poverty-rate change:       {(cf_rate - base_rate) * 100:+.2f} pp", flush=True)

    # Decile breakdown of SPM resources change
    deciles_base = decile_summary(
        base_resources["spm_resources"], base_resources["weight"]
    ).rename(columns={"mean": "spm_mean_baseline", "total": "spm_total_baseline"})
    deciles_cf = decile_summary(
        cf_resources["spm_resources"], cf_resources["weight"]
    ).rename(columns={"mean": "spm_mean_cut", "total": "spm_total_cut"})
    out_df = deciles_base[["count_weighted", "spm_mean_baseline", "spm_total_baseline"]].join(
        deciles_cf[["spm_mean_cut", "spm_total_cut"]]
    )
    out_df["mean_delta_$"] = out_df["spm_mean_cut"] - out_df["spm_mean_baseline"]
    out_df["total_delta_$M"] = (out_df["spm_total_cut"] - out_df["spm_total_baseline"]) / 1e6
    out_df.to_csv(args.out)

    print(f"\nDecile breakdown saved: {args.out}", flush=True)
    print("\nPer-decile mean SPM resource change ($, baseline → cut):", flush=True)
    print(out_df[["spm_mean_baseline", "spm_mean_cut", "mean_delta_$"]].round(0).to_string(),
          flush=True)

    # --by-puma: per-PUMA poverty rate + mean SPM resources, baseline vs cut
    if args.by_puma:
        from tax_modeler import poverty_by_geography, threshold_for_units
        puma_out = args.out.with_name(args.out.stem + "_by_puma.csv")
        thr = threshold_for_units(base_resources, year=2024)
        base_geo = poverty_by_geography(
            base_resources, resources_col="spm_resources",
            threshold=thr, geo_col="PUMA",
        ).rename(columns={
            "poverty_rate": "poverty_rate_baseline",
            "mean_resources_$": "mean_resources_baseline_$",
        })
        thr_cf = threshold_for_units(cf_resources, year=2024)
        cf_geo = poverty_by_geography(
            cf_resources, resources_col="spm_resources",
            threshold=thr_cf, geo_col="PUMA",
        ).rename(columns={
            "poverty_rate": "poverty_rate_cut",
            "mean_resources_$": "mean_resources_cut_$",
        })
        joined = base_geo[["count_weighted", "poverty_rate_baseline", "mean_resources_baseline_$"]] \
            .join(cf_geo[["poverty_rate_cut", "mean_resources_cut_$"]])
        joined["poverty_rate_delta_pp"] = (
            joined["poverty_rate_cut"] - joined["poverty_rate_baseline"]
        ) * 100
        # Suppression: flag PUMAs below min count
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
