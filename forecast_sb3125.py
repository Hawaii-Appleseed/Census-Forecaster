"""SB 3125 fiscal-impact forecast for TY 2027 through 2031.

Compares projected revenue under:
  - Baseline:  Act 46 as currently enacted (Hawaii's 2024 tax-cut phase-in)
  - Scenario:  SB 3125 CD1 or CD2 (2026 conference draft) which overrides §235-51

Usage:
  python forecast_sb3125.py --cd 1   # SB 3125 CD1 (default)
  python forecast_sb3125.py --cd 2   # SB 3125 CD2

Replaces the two separate scripts:
  forecast_sb3125_cd1.py  (identical except TaxSystemRegistry call + output path)
  forecast_sb3125_cd2.py  (identical except TaxSystemRegistry call + output path)

Output (written to /tmp/):
  sb3125_cd{N}_fiscal_impact_2027_2031.csv
  sb3125_cd{N}_decile_TY2027.csv
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

from _forecast_common import (
    CACHE_FILE, DATA_DIR, TARGET_YEARS, parse_cd_args, silence_noise,
)

if __name__ == "__main__":
    silence_noise()

    args = parse_cd_args(__doc__)
    CD = args.cd  # "1" or "2"
    OUT_CSV = Path(f"/tmp/sb3125_cd{CD}_fiscal_impact_2027_2031.csv")

    try:
        import time
        import pandas as pd

        print(f"SB 3125 CD{CD} fiscal-impact forecast", flush=True)
        print("Importing modules...", flush=True)
        from tax_modeler.pipeline import _enrich_for_credits, _compute_base_tax, _calibrate
        from tax_modeler.loaders.pums_loader import PUMSDataLoader
        from tax_modeler.units.constructor import TaxUnitConstructor
        from tax_modeler.projection.tax_unit_projector import project_tax_units_forward
        from tax_modeler.config.tax_system_config import (
            TaxCalculator, TaxSystemRegistry, compare_systems,
        )
        from tax_modeler.scenarios.sb3125_cd1_credits import compute_credit_overlay

        wall_start = time.perf_counter()

        from tax_modeler.artifacts import (
            check_cache_sidecar, load_canonical_deduction_params, write_cache_sidecar,
        )

        # ---- Build calibrated base-year units (cached) ---------------------
        if CACHE_FILE.exists():
            print(f"Loading cached units from {CACHE_FILE}...", flush=True)
            check_cache_sidecar(CACHE_FILE)
            units = pd.read_parquet(CACHE_FILE)
            print(f"  {len(units):,} units loaded from cache", flush=True)
        else:
            t0 = time.perf_counter()
            print("Loading PUMS...", flush=True)
            loader = PUMSDataLoader(data_dir=DATA_DIR)
            person_df, hh_df = loader.load_data(state="15", pums_type="5yr")
            print(f"  {len(person_df):,} persons, {len(hh_df):,} households in {time.perf_counter()-t0:.1f}s", flush=True)

            t0 = time.perf_counter()
            print("Constructing tax units...", flush=True)
            constructor = TaxUnitConstructor(
                person_df, hh_df,
                use_soi_calibration=False,
                progress_bar=False,
            )
            units = constructor.create_rule_based_units(parallel=False)
            print(f"  {len(units):,} units in {time.perf_counter()-t0:.1f}s", flush=True)

            safe_units = units.copy()
            for col in safe_units.columns:
                if safe_units[col].dtype == object:
                    safe_units[col] = safe_units[col].astype(str)
            CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
            safe_units.to_parquet(CACHE_FILE, index=False)
            write_cache_sidecar(CACHE_FILE, extra={
                "built_by": "forecast_sb3125.py",
                "pums": "HI 5yr 2020-24",
                "n_units": int(len(safe_units)),
            })
            print(f"  Units cached to {CACHE_FILE}", flush=True)
            del safe_units

        t0 = time.perf_counter()
        print("Enriching + base tax + calibrating...", flush=True)
        # Score with the canonical itemized-deduction params — bare
        # _compute_base_tax (SD-only) diverged from the calibrated basis (C3).
        CAL_DED_PARAMS = load_canonical_deduction_params()
        units = _enrich_for_credits(units)
        units = _compute_base_tax(units, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        calibrated = _calibrate(units)
        print(f"  Done in {time.perf_counter()-t0:.1f}s", flush=True)

        # ---- Top-income synthesis ------------------------------------------
        from tax_modeler.scenarios.top_income_synthesis import (
            synthesize_top_filers, validate_top_synthesis,
        )
        t0 = time.perf_counter()
        print("Synthesizing top-income ($1M+) filers...", flush=True)
        before = validate_top_synthesis(calibrated)
        print(f"  Before: {before['filers_1m_plus']:,.0f} filers "
              f"({100*before['filer_target_ratio']:.1f}% of target), "
              f"${before['tax_1m_plus_$M']:,.1f}M tax "
              f"({100*before['tax_target_ratio']:.1f}% of the DOTAX target)", flush=True)
        calibrated = synthesize_top_filers(calibrated)
        calibrated = _compute_base_tax(calibrated, deduction_params=CAL_DED_PARAMS, tax_year=2023)
        after = validate_top_synthesis(calibrated)
        print(f"  After:  {after['filers_1m_plus']:,.0f} filers "
              f"({100*after['filer_target_ratio']:.1f}% of target), "
              f"${after['tax_1m_plus_$M']:,.1f}M tax "
              f"({100*after['tax_target_ratio']:.1f}% of the DOTAX target)", flush=True)
        print(f"  Filing mix at $1M+: MFJ {100*after['mfj_share']:.0f}% / "
              f"Single {100*after['single_share']:.0f}% / "
              f"HoH {100*after['hoh_share']:.0f}% / "
              f"MFS {100*after['mfs_share']:.0f}%", flush=True)
        print(f"  Done in {time.perf_counter()-t0:.1f}s", flush=True)

        # ---- Per-year fiscal impact ----------------------------------------
        COR_FY27_IIT_PROJ_M = 3050.0

        # Select the right TaxSystemRegistry factory for this CD
        get_scenario_system = (
            TaxSystemRegistry.get_sb3125_cd1_system if CD == "1"
            else TaxSystemRegistry.get_sb3125_cd2_system
        )

        calc = TaxCalculator()
        rows = []
        decile_snapshot = None
        for year in TARGET_YEARS:
            t0 = time.perf_counter()
            print(f"\nProjecting + scoring TY {year}...", flush=True)
            projected = project_tax_units_forward(
                calibrated, target_year=year, method="ensemble"
            )

            baseline_cfg = TaxSystemRegistry.get_act46_system(year)
            scenario_cfg = get_scenario_system(year)
            cmp = compare_systems(projected, baseline_cfg, scenario_cfg, calculator=calc)

            base_row = cmp[cmp["system"] == baseline_cfg.name].iloc[0]
            scen_row = cmp[cmp["system"] == scenario_cfg.name].iloc[0]
            diff_row = cmp[cmp["system"] == "Difference"].iloc[0]

            credit = compute_credit_overlay(year)

            row = {
                "tax_year": year,
                "act46_revenue_$M":                    round(base_row["revenue_millions"], 2),
                f"sb3125_cd{CD}_revenue_$M":           round(scen_row["revenue_millions"], 2),
                "bracket_delta_$M":                    round(diff_row["revenue_millions"], 2),
                "reec_baseline_$M":                    credit["reec_baseline_$M"],
                "reec_eligible_$M":                    credit["reec_eligible_$M"],
                "reec_savings_$M":                     credit["reec_savings_$M"],
                "cgec_savings_$M":                     credit["cgec_savings_$M"],
                "tcra_savings_$M":                     credit["tcra_savings_$M"],
                "total_credit_savings_$M":             credit["total_credit_savings_$M"],
                "total_impact_$M":                     round(
                    diff_row["revenue_millions"] + credit["total_credit_savings_$M"], 2
                ),
            }
            rows.append(row)
            print(f"  TY {year}: bracket_delta={row['bracket_delta_$M']:+.1f}M  "
                  f"credit_savings={row['total_credit_savings_$M']:+.1f}M  "
                  f"total={row['total_impact_$M']:+.1f}M  "
                  f"({time.perf_counter()-t0:.1f}s)", flush=True)

            if year == 2027:
                proj = projected.copy()
                base_results = []
                scen_results = []
                for inc, fs, ndep in zip(proj["income"], proj["filing_status"],
                                         proj.get("num_dependents", [0]*len(proj))):
                    b = calc.calculate_tax(inc, baseline_cfg, fs, num_exemptions=int(ndep)+1)
                    s = calc.calculate_tax(inc, scenario_cfg, fs, num_exemptions=int(ndep)+1)
                    base_results.append(b["tax_liability"])
                    scen_results.append(s["tax_liability"])
                proj["base_tax"] = base_results
                proj["scen_tax"] = scen_results
                proj["delta_per_unit"] = proj["scen_tax"] - proj["base_tax"]

                proj_sorted = proj.sort_values("income").reset_index(drop=True)
                cumw = proj_sorted["weight"].cumsum()
                total_w = float(cumw.iloc[-1])
                proj_sorted["decile"] = pd.cut(
                    cumw / total_w,
                    bins=[i / 10 for i in range(11)],
                    labels=[f"D{i}" for i in range(1, 11)],
                    include_lowest=True,
                )
                grp = proj_sorted.groupby("decile", observed=True)
                decile_snapshot = pd.DataFrame({
                    "n_filers":              grp["weight"].sum(),
                    "income_min_$":          grp["income"].min(),
                    "income_max_$":          grp["income"].max(),
                    "avg_income_$":          grp.apply(lambda d: (d["income"] * d["weight"]).sum() / d["weight"].sum()),
                    "base_tax_total_$M":     grp.apply(lambda d: (d["base_tax"] * d["weight"]).sum()) / 1e6,
                    "scen_tax_total_$M":     grp.apply(lambda d: (d["scen_tax"] * d["weight"]).sum()) / 1e6,
                    "delta_total_$M":        grp.apply(lambda d: (d["delta_per_unit"] * d["weight"]).sum()) / 1e6,
                    "avg_delta_per_filer_$": grp.apply(
                        lambda d: (d["delta_per_unit"] * d["weight"]).sum() / d["weight"].sum()
                    ),
                }).reset_index()

        # ---- Output -----------------------------------------------------------
        df = pd.DataFrame(rows)
        df.to_csv(OUT_CSV, index=False)

        print("\n" + "=" * 110, flush=True)
        print(f"SB 3125 CD{CD} FISCAL IMPACT ($ millions, vs. Act 46 baseline)", flush=True)
        print(f"  REEC demand scenario: obbba_mid (SEIA-anchored, Hawaii-tempered)", flush=True)
        print(f"  Top-income synthesis: Pareto alpha=1.5, target 1,704 filers / $441M tax (DOTAX TY2023)", flush=True)
        print("=" * 110, flush=True)
        print(df.to_string(index=False), flush=True)
        print("\nNote: positive total_impact_$M = revenue gained for the State", flush=True)
        print(f"5-year cumulative: ${df['total_impact_$M'].sum():,.0f}M", flush=True)

        ty27_baseline = df.loc[df["tax_year"] == 2027, "act46_revenue_$M"].iloc[0]
        gap = COR_FY27_IIT_PROJ_M - ty27_baseline
        print("\n" + "-" * 110, flush=True)
        print("BASELINE VALIDATION vs Hawaii Council on Revenues (Sep 2025 forecast)", flush=True)
        print(f"  COR FY27 individual income tax projection:  ${COR_FY27_IIT_PROJ_M:,.0f}M", flush=True)
        print(f"  Microsim TY27 act46 baseline:               ${ty27_baseline:,.0f}M", flush=True)
        print(f"  Gap:                                        ${gap:,.0f}M ({100*gap/COR_FY27_IIT_PROJ_M:.0f}% of COR)", flush=True)
        print("  Gap is expected — microsim excludes PTE pass-throughs, non-resident", flush=True)
        print("  withholding, audit/penalty assessments, and late filings.", flush=True)
        print("  Use the bracket DELTA for fiscal-impact magnitude.", flush=True)

        if decile_snapshot is not None:
            print("\n" + "-" * 110, flush=True)
            print("DISTRIBUTIONAL IMPACT BY INCOME DECILE (TY 2027, $ millions and per-filer $)", flush=True)
            print("-" * 110, flush=True)
            with pd.option_context("display.float_format", "{:,.2f}".format):
                print(decile_snapshot.to_string(index=False), flush=True)
            decile_csv = Path(f"/tmp/sb3125_cd{CD}_decile_TY2027.csv")
            decile_snapshot.to_csv(decile_csv, index=False)
            print(f"  Saved: {decile_csv}", flush=True)

        print(f"\nSaved: {OUT_CSV}", flush=True)
        print(f"Total elapsed: {time.perf_counter() - wall_start:.1f}s", flush=True)

    except Exception as e:
        print(f"\nERROR: {e}", flush=True)
        traceback.print_exc()
        sys.exit(1)
