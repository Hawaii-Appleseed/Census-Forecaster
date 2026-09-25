"""Hawaii state EITC revert 40% → 20% of federal: quintile + poverty impact.

Scenario: revert the post-Act-209 (2023) Hawaii state EITC rate from 40%
of federal back to 20% of federal, effective tax year 2028.

**TY 2028 caveat**: federal EITC/CTC parameter tables in this package
stop at TY 2025 (Rev. Proc. 2024-40). TCJA also expires 12/31/2025,
which materially changes federal CTC refundability post-2025. This run
uses TY 2025 federal parameters as a placeholder; treat the numbers as
"TY 2028 under TY 2025 federal credit law plus current-law HI EITC".
The HI EITC change itself (the policy in question) is exact — it is a
fixed percentage of the federal credit, and we change only that
percentage.

Two SPM-poverty scenarios run side-by-side:
  * ``hi_eitc_revert_20``            — static counterfactual (50% HI EITC cut)
  * ``hi_eitc_revert_20_behavioral`` — adds extensive-margin single-mother
    LFP exit response (Meyer-Rosenbaum 2001 elasticity, default 0.5).
    The behavioral channel is the difference between the two columns at
    the HoH (single-mother proxy) cut.

Outputs
-------
    reports/eitc_revert_20_<year>/
        by_quintile.csv         — avg tax change per filer, by income quintile
        by_state.csv            — SPM poverty baseline + both scenarios (state)
        by_county.csv           — same, per county
        by_household_type.csv   — same, per filing-status (single/HoH/MFJ/MFS)
        spm_units.parquet       — SPM-unit-grain frame with all scenario cols
        summary.txt             — printed summary

Usage
-----
    uv run python forecast_hi_eitc_revert_20.py \\
        --tax-year 2025 \\
        --pums-data-dir packages/data/raw/pums_2024_1yr \\
        --out reports/eitc_revert_20_2028/

    # Disable the behavioral scenario:
    uv run python forecast_hi_eitc_revert_20.py --no-behavioral

    # Sensitivity sweep on the LFP elasticity:
    uv run python forecast_hi_eitc_revert_20.py --lfp-elasticity 0.3
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

LOG = logging.getLogger("forecast_hi_eitc_revert_20")
REPO_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tax-year", type=int, default=2025,
                   help="Tax year for the run. Defaults to 2025 (the latest "
                        "year for which federal EITC/CTC parameter tables are "
                        "populated). See module docstring for the TY 2028 caveat.")
    p.add_argument("--pums-data-dir", type=Path,
                   default=REPO_ROOT / "packages" / "data" / "raw" / "pums_2024_1yr",
                   help="Directory containing psam_p15 and psam_h15 PUMS files.")
    p.add_argument("--use-fixture", action="store_true", default=False,
                   help="Use the synthetic test fixture (57 units). Smoke test only.")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "reports" / "eitc_revert_20_2028",
                   help="Output directory.")
    p.add_argument("--lfp-elasticity", type=float, default=0.5,
                   help="Single-mother LFP elasticity wrt combined EITC value "
                        "(Meyer-Rosenbaum 2001 midpoint; literature range "
                        "0.3-0.7). Default 0.5. Set 0 to disable.")
    p.add_argument("--no-behavioral", dest="behavioral", action="store_false",
                   default=True,
                   help="Skip the LFP behavioral scenario; run only the static "
                        "counterfactual.")
    p.add_argument("--no-calibrate-hi-eitc-takeup",
                   dest="calibrate_hi_eitc_takeup", action="store_false",
                   default=True,
                   help="Skip HI EITC take-up calibration (DOTAX/IRS-anchored). "
                        "Default ON: drops smallest-credit eligibles until the "
                        "weighted claimer count matches the hawaii_caseload.csv "
                        "anchor for hi_eitc.")
    p.add_argument("--no-snap-offset", dest="snap_offset", action="store_false",
                   default=True,
                   help="Skip the SNAP cascading offset to the LFP exit channel "
                        "(1B). Default ON: recomputes SNAP on the counterfactual "
                        "frame where affected filers have earned_income=0, "
                        "subtracts the SNAP gain from the LFP resource loss.")
    p.add_argument("--intensive-elasticity-phase-in", type=float, default=0.15,
                   help="Hours elasticity for HoH filers in the EITC phase-in "
                        "region (1C). Default 0.15 (Eissa-Hoynes 2004 midpoint; "
                        "literature range 0.10-0.30). Set 0 to disable.")
    p.add_argument("--bootstrap-replicates", type=int, default=250,
                   help="Number of bootstrap replicates for the 90%% CI on "
                        "persons-newly-poor and children-newly-poor (1D). "
                        "Default 250. Set 0 to skip the bootstrap.")
    p.add_argument("--bootstrap-seed", type=int, default=42,
                   help="Seed for the bootstrap resampler. Default 42.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _quintile_breakdown(units: pd.DataFrame) -> pd.DataFrame:
    """Avg per-filer tax change by income quintile under the 40% → 20% revert.

    Tax change per filer = +0.5 × hi_eitc_amount (less refundable credit ⇒
    higher net tax liability). Quintiles are weighted by ``weight``, ranked
    on ``income``. Reports both per-filer tax change and the aggregate flow.
    """
    from tax_modeler.metrics.distribution import weighted_ntile_labels

    qlabels = weighted_ntile_labels(
        units["income"], units["weight"], n=5, label_prefix="Q"
    )
    rows = []
    for q in [f"Q{i}" for i in range(1, 6)]:
        mask = (qlabels == q).to_numpy()
        if not mask.any():
            continue
        w = units.loc[mask, "weight"].to_numpy(dtype=float)
        hi_eitc = units.loc[mask, "hi_eitc_amount"].fillna(0).to_numpy(dtype=float)
        inc = units.loc[mask, "income"].to_numpy(dtype=float)

        tax_change_per_filer = 0.5 * hi_eitc  # dollars/year, per filer
        wsum = w.sum()
        avg_inc = float((inc * w).sum() / wsum)
        avg_tax_change = float((tax_change_per_filer * w).sum() / wsum)
        agg_tax_change_m = float((tax_change_per_filer * w).sum() / 1e6)
        share_affected = float((w[hi_eitc > 0]).sum() / wsum) * 100
        rows.append({
            "quintile": q,
            "n_filers_weighted": float(wsum),
            "avg_income": round(avg_inc, 0),
            "share_affected_pct": round(share_affected, 1),
            "avg_tax_change_$": round(avg_tax_change, 2),
            "aggregate_tax_change_$M": round(agg_tax_change_m, 3),
        })
    return pd.DataFrame(rows)


def _children_newly_poor(
    units: pd.DataFrame, scenario: str
) -> float:
    """Children (n_children-weighted persons) crossing into poverty in a scenario.

    Operates on the SPM-unit frame (output of compute_poverty_impact, i.e.
    result.units). Returns weighted child count newly below threshold.
    """
    thr = units["spm_threshold"].to_numpy(dtype=float)
    base_poor = units["spm_resources"].to_numpy(dtype=float) < thr
    scn_poor = units[f"spm_resources_{scenario}"].to_numpy(dtype=float) < thr
    newly_poor = scn_poor & ~base_poor
    n_kids = units["n_children"].fillna(0).to_numpy(dtype=float)
    w = units["weight"].astype(float).to_numpy()
    return float((n_kids[newly_poor] * w[newly_poor]).sum())


def _bootstrap_persons_newly_poor(
    units: pd.DataFrame,
    scenarios: list,
    *,
    n_replicates: int = 250,
    seed: int = 42,
) -> dict:
    """Weighted bootstrap CI for persons-newly-poor and children-newly-poor.

    Resamples SPM units with replacement (probability proportional to
    ``weight``). For each replicate, the persons-newly-poor count is the
    sum over sampled rows of ``n_persons × is_newly_poor`` (×1 weight,
    because the sample is already weight-proportional). Similarly for
    children-newly-poor using ``n_children``.

    Returns {scenario: {'persons': {'p5','p50','p95'},
                        'children': {'p5','p50','p95'}}}.
    """
    if n_replicates <= 0:
        return {}

    rng = np.random.default_rng(seed)
    thr = units["spm_threshold"].to_numpy(dtype=float)
    base_poor = units["spm_resources"].to_numpy(dtype=float) < thr
    n_persons = units["n_persons"].fillna(0).to_numpy(dtype=float)
    n_children = units["n_children"].fillna(0).to_numpy(dtype=float)
    w = units["weight"].astype(float).to_numpy()
    w_sum = w.sum()
    probs = w / w_sum
    n_rows = len(units)

    # Per-scenario binary newly-poor flag, precomputed once.
    newly_poor_by_scn = {
        scn: (units[f"spm_resources_{scn}"].to_numpy(dtype=float) < thr) & ~base_poor
        for scn in scenarios
    }

    # Each replicate: sample n_rows indices with replacement weighted by probs;
    # sum n_persons * newly_poor at the sampled indices, then rescale so the
    # totals are on the original weight basis (multiply by w_sum / n_rows).
    rescale = w_sum / n_rows
    out: dict = {}
    for scn, flag in newly_poor_by_scn.items():
        persons_rep = np.empty(n_replicates)
        children_rep = np.empty(n_replicates)
        person_contrib = n_persons * flag
        child_contrib = n_children * flag
        for i in range(n_replicates):
            idx = rng.choice(n_rows, size=n_rows, replace=True, p=probs)
            persons_rep[i] = person_contrib[idx].sum() * rescale
            children_rep[i] = child_contrib[idx].sum() * rescale
        out[scn] = {
            "persons": {
                "p5": float(np.percentile(persons_rep, 5)),
                "p50": float(np.percentile(persons_rep, 50)),
                "p95": float(np.percentile(persons_rep, 95)),
            },
            "children": {
                "p5": float(np.percentile(children_rep, 5)),
                "p50": float(np.percentile(children_rep, 50)),
                "p95": float(np.percentile(children_rep, 95)),
            },
        }
    return out


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Reuse the production pipeline helpers from poverty_impact_report.
    import poverty_impact_report as pir

    args.out.mkdir(parents=True, exist_ok=True)

    LOG.info("Loading PUMS units (real PUMS=%s)", not args.use_fixture)
    base_units, persons = pir._load_units(args.pums_data_dir, args.use_fixture)

    from tax_modeler.pipeline import enrich_with_spm_unit_id
    base_units, persons = enrich_with_spm_unit_id(base_units, persons)

    PUMS_CONSTRUCTION_YEAR = 2022
    project_required = args.tax_year != PUMS_CONSTRUCTION_YEAR
    units = pir._build_units_for_tax_year(base_units, args.tax_year, project=project_required)

    # Lift the short 1-/2-child EITC buckets to IRS Hawaii counts before
    # take-up, as poverty_impact_report does by default. Without it the
    # federal EITC base runs ~25% short and the HI EITC ~$57M vs DOTAX's $77M.
    if not args.use_fixture:
        units = pir._apply_eitc_reweight(units)
    units = pir._apply_credit_takeup(units, tax_year=args.tax_year)
    units = pir._apply_hi_eitc(units, tax_year=args.tax_year)

    # 1A: HI EITC take-up calibration. Federal EITC was already calibrated
    # to IRS SOI via the line above; this drops filers who claim federal
    # but not state EITC, anchored to the DOTAX TY 2023 actuals (Table A-2:
    # 84,470 returns, Table A-1: $77.054M). Year pinned to 2023 because
    # that is the first post-refundability year (Act 209, 2023) — using
    # earlier years would understate take-up dramatically.
    HI_EITC_ANCHOR_YEAR = 2023
    hi_eitc_takeup_diag = None
    if args.calibrate_hi_eitc_takeup:
        w = units["weight"].astype(float).to_numpy()
        hi_before = units["hi_eitc_amount"].fillna(0).to_numpy(dtype=float)
        before_count = float(w[hi_before > 0].sum())
        before_dollars_m = float((hi_before * w).sum() / 1e6)
        units = pir._apply_credit_takeup(
            units, tax_year=HI_EITC_ANCHOR_YEAR, programs=("hi_eitc",),
        )
        hi_after = units["hi_eitc_amount"].fillna(0).to_numpy(dtype=float)
        w = units["weight"].astype(float).to_numpy()
        after_count = float(w[hi_after > 0].sum())
        after_dollars_m = float((hi_after * w).sum() / 1e6)
        hi_eitc_takeup_diag = {
            "before_count_weighted": before_count,
            "after_count_weighted": after_count,
            "before_dollars_$M": before_dollars_m,
            "after_dollars_$M": after_dollars_m,
            "dropped_pct": (1 - after_count / before_count) * 100 if before_count > 0 else 0.0,
        }
        LOG.info("HI EITC take-up calibration: %s", hi_eitc_takeup_diag)

    units = pir._apply_arpa_ctc(units)
    units = pir._apply_snap(units, tax_year=args.tax_year)

    from tax_modeler.liability.federal import compute_federal_income_tax_for_units
    units = compute_federal_income_tax_for_units(units, tax_year=args.tax_year)

    from tax_modeler.benefits.moop import compute_moop_for_units
    units = compute_moop_for_units(units)
    units = pir._apply_housing_subsidy(units, tax_year=args.tax_year)
    units = pir._apply_childcare_subsidy(units, tax_year=args.tax_year)
    units = pir._apply_wic(units, tax_year=args.tax_year)
    units = pir._apply_liheap(units, tax_year=args.tax_year)
    from tax_modeler.benefits.school_lunch import compute_school_lunch_for_units
    units = compute_school_lunch_for_units(units, tax_year=args.tax_year)
    from tax_modeler.benefits.childcare_expense import compute_childcare_expense_for_units
    from tax_modeler.benefits.work_expense import compute_work_expense_for_units
    units = compute_childcare_expense_for_units(units)
    units = compute_work_expense_for_units(units)

    # Behavioral response (extensive-margin LFP for HoH filers). Must run
    # BEFORE aggregate_to_spm_units so the loss column gets summed into
    # the SPM-unit-grain frame via _SUM_COLS.
    lfp_diag = None
    snap_offset_diag = None
    intensive_diag = None
    if args.behavioral and args.lfp_elasticity > 0:
        from tax_modeler.scenarios.eitc_labor_response import (
            apply_hi_eitc_intensive_response,
            apply_hi_eitc_lfp_response,
            compute_snap_offset_for_lfp_exit,
        )
        units, lfp_diag = apply_hi_eitc_lfp_response(
            units, elasticity=args.lfp_elasticity,
        )
        LOG.info("LFP behavioral response: %s", lfp_diag)
        # 1B: SNAP cascading offset to the LFP exit channel.
        if args.snap_offset:
            units, snap_offset_diag = compute_snap_offset_for_lfp_exit(units)
            LOG.info("SNAP offset: %s", snap_offset_diag)
        else:
            units["lfp_behavioral_snap_offset"] = 0.0
        # 1C: Intensive-margin (hours) response for phase-in HoH workers.
        if args.intensive_elasticity_phase_in > 0:
            units, intensive_diag = apply_hi_eitc_intensive_response(
                units,
                elasticity_phase_in=args.intensive_elasticity_phase_in,
                tax_year=args.tax_year,
            )
            LOG.info("Intensive-margin response: %s", intensive_diag)
        else:
            units["intensive_resource_loss"] = 0.0
    else:
        units["lfp_behavioral_resource_loss"] = 0.0
        units["lfp_behavioral_snap_offset"] = 0.0
        units["intensive_resource_loss"] = 0.0

    # Per-quintile tax change (on the tax-unit frame, before SPM rollup).
    quint = _quintile_breakdown(units)
    quint.to_csv(args.out / "by_quintile.csv", index=False)

    # Poverty impact via SPM-unit-grained rollup.
    from tax_modeler.poverty.spm_aggregation import aggregate_to_spm_units
    poverty_frame = aggregate_to_spm_units(units, persons)
    LOG.info("Aggregated %d tax units -> %d SPM units", len(units), len(poverty_frame))

    scenarios = ["hi_eitc_revert_20"]
    if args.behavioral:
        scenarios.append("hi_eitc_revert_20_behavioral")

    from tax_modeler.poverty.impact import compute_poverty_impact
    result = compute_poverty_impact(
        poverty_frame, tax_year=args.tax_year,
        scenarios=tuple(scenarios),
    )
    result.by_state.to_csv(args.out / "by_state.csv", index=False)
    result.by_county.to_csv(args.out / "by_county.csv", index=False)
    result.by_household_type.to_csv(args.out / "by_household_type.csv", index=False)
    result.units.to_parquet(args.out / "spm_units.parquet")

    # Summary block.
    s = result.by_state.iloc[0]
    lines = []
    lines.append("=" * 76)
    lines.append(f"HI state EITC revert: 40% → 20% of federal (TY {args.tax_year} parameters)")
    lines.append("=" * 76)
    lines.append("")
    lines.append("Per-quintile average tax change (per filer, $/year):")
    lines.append(quint.to_string(index=False))
    lines.append("")
    total_tax_change_m = float(quint["aggregate_tax_change_$M"].sum())
    lines.append(f"Aggregate state revenue gain (= taxpayer loss): ${total_tax_change_m:,.1f}M")
    lines.append("")
    lines.append("SPM poverty (Hawaii, all persons):")
    lines.append(f"  Baseline rate                       : {s['poverty_rate_baseline'] * 100:>7.2f}%")

    static_rate = s["poverty_rate_hi_eitc_revert_20"]
    static_persons = s["persons_lifted_hi_eitc_revert_20"]
    static_gap_m = s["gap_closed_hi_eitc_revert_20_$"] / 1e6
    lines.append(f"  Revert rate (static)                : {static_rate * 100:>7.2f}%")
    lines.append(f"  Change (static)                     : {(static_rate - s['poverty_rate_baseline']) * 100:>+7.2f} pp")
    lines.append(f"  Additional persons in poverty       : {static_persons:>10,.0f}")
    lines.append(f"  Additional poverty gap              : ${static_gap_m:>+9,.1f}M")

    if args.behavioral:
        beh_rate = s["poverty_rate_hi_eitc_revert_20_behavioral"]
        beh_persons = s["persons_lifted_hi_eitc_revert_20_behavioral"]
        beh_gap_m = s["gap_closed_hi_eitc_revert_20_behavioral_$"] / 1e6
        lines.append("")
        lines.append("  + LFP behavioral channel (single-mother extensive margin):")
        lines.append(f"    Revert rate (incl. behavioral)    : {beh_rate * 100:>7.2f}%")
        lines.append(f"    Change (incl. behavioral)         : {(beh_rate - s['poverty_rate_baseline']) * 100:>+7.2f} pp")
        lines.append(f"    Additional persons (incl. beh.)   : {beh_persons:>10,.0f}")
        lines.append(f"    Additional poverty gap (incl. beh): ${beh_gap_m:>+9,.1f}M")
        lines.append(f"    → behavioral CHANNEL ADDS         : {beh_persons - static_persons:>+10,.0f} persons, "
                     f"${beh_gap_m - static_gap_m:+,.1f}M gap")

    # HoH (single-mother proxy) — composition-derived (1 adult + ≥1 child).
    lines.append("")
    lines.append("Single-mother proxy (HoH composition: 1 adult + ≥1 child):")
    lines.append(f"  Weighted persons in HoH units       : {s['weighted_persons_hoh']:>10,.0f}")
    lines.append(f"  Baseline HoH poverty rate           : {s['poverty_rate_hoh_baseline'] * 100:>7.2f}%")
    lines.append(f"  Static revert HoH rate              : {s['poverty_rate_hi_eitc_revert_20_hoh'] * 100:>7.2f}%")
    lines.append(f"  Additional HoH persons (static)     : {s['persons_lifted_hi_eitc_revert_20_hoh']:>10,.0f}")
    if args.behavioral:
        lines.append(f"  Behavioral revert HoH rate          : {s['poverty_rate_hi_eitc_revert_20_behavioral_hoh'] * 100:>7.2f}%")
        lines.append(f"  Additional HoH persons (behavioral) : {s['persons_lifted_hi_eitc_revert_20_behavioral_hoh']:>10,.0f}")

    # Children newly in poverty
    lines.append("")
    lines.append("Children newly in poverty (SPM-unit n_children weighted):")
    for scn in scenarios:
        kids = _children_newly_poor(result.units, scn)
        label = "static" if scn == "hi_eitc_revert_20" else "+ behavioral"
        lines.append(f"  {label:<32}            : {kids:>10,.0f}")

    if hi_eitc_takeup_diag is not None:
        lines.append("")
        lines.append("HI EITC take-up calibration (1A):")
        lines.append(f"  Pre-calibration claimers (weighted) : {hi_eitc_takeup_diag['before_count_weighted']:>10,.0f}")
        lines.append(f"  Post-calibration claimers (weighted): {hi_eitc_takeup_diag['after_count_weighted']:>10,.0f}")
        lines.append(f"  Pre-calibration HI EITC dollars     : ${hi_eitc_takeup_diag['before_dollars_$M']:>9,.1f}M")
        lines.append(f"  Post-calibration HI EITC dollars    : ${hi_eitc_takeup_diag['after_dollars_$M']:>9,.1f}M")
        lines.append(f"  Filers dropped (smallest credits)   : {hi_eitc_takeup_diag['dropped_pct']:>9,.1f}%")

    if lfp_diag is not None:
        lines.append("")
        lines.append("LFP behavioral diagnostics (extensive margin, 1B/1C precursor):")
        lines.append(f"  Elasticity (η)                      : {lfp_diag['elasticity']:.2f}")
        lines.append(f"  Δlog(combined federal+HI EITC)      : {lfp_diag['delta_log_eitc']:>+7.4f}")
        lines.append(f"  Per-filer exit probability          : {lfp_diag['p_exit']:>7.4f}")
        lines.append(f"  Affected HoH filers (weighted)      : {lfp_diag['affected_filers_weighted']:>10,.0f}")
        lines.append(f"  Expected LFP exits (weighted)       : {lfp_diag['expected_lfp_exits_weighted']:>10,.0f}")
        lines.append(f"  Raw extensive resource loss         : ${lfp_diag['aggregate_resource_loss_$M']:>9,.2f}M")

    if snap_offset_diag is not None:
        lines.append("")
        lines.append("SNAP cascading offset (1B):")
        lines.append(f"  Aggregate SNAP gain (LFP exits)     : ${snap_offset_diag['aggregate_snap_offset_$M']:>9,.2f}M")
        lines.append(f"  Avg SNAP gain per exit              : ${snap_offset_diag['avg_snap_gain_per_exit']:>9,.0f}/yr")

    if intensive_diag is not None:
        lines.append("")
        lines.append("Intensive-margin (hours) response (1C):")
        lines.append(f"  Elasticity (phase-in only)          : {intensive_diag['elasticity_phase_in']:.2f}")
        lines.append(f"  Affected phase-in filers (weighted) : {intensive_diag['affected_filers_weighted']:>10,.0f}")
        lines.append(f"  Avg |Δhours|                        : {intensive_diag['avg_abs_hours_change_pct']:>7.2f}%")
        lines.append(f"  Aggregate intensive resource loss   : ${intensive_diag['aggregate_resource_loss_$M']:>9,.2f}M")

    # Bootstrap 90% CI (1D).
    if args.bootstrap_replicates > 0:
        ci = _bootstrap_persons_newly_poor(
            result.units, scenarios,
            n_replicates=args.bootstrap_replicates, seed=args.bootstrap_seed,
        )
        if ci:
            lines.append("")
            lines.append(f"Bootstrap 90% CI ({args.bootstrap_replicates} replicates, seed={args.bootstrap_seed}):")
            for scn in scenarios:
                p = ci[scn]["persons"]
                k = ci[scn]["children"]
                lines.append(f"  {scn}:")
                lines.append(f"    persons newly poor p5/p50/p95  : "
                             f"{p['p5']:>8,.0f} / {p['p50']:>8,.0f} / {p['p95']:>8,.0f}")
                lines.append(f"    children newly poor p5/p50/p95 : "
                             f"{k['p5']:>8,.0f} / {k['p50']:>8,.0f} / {k['p95']:>8,.0f}")

    lines.append("")
    lines.append("Caveats:")
    lines.append("- TY 2025 federal EITC/CTC parameters used as TY 2028 proxy.")
    lines.append("- TCJA expiration (12/31/2025) not modeled. HI EITC ratio change is exact.")
    if hi_eitc_takeup_diag is not None:
        lines.append("- HI EITC take-up anchored to DOTAX Tax Credits Claimed TY 2023 "
                     "(84,470 returns, $77.05M; first refundable year per Act 209, 2023). "
                     "Effective take-up ≈ 100% — refundable HI EITC auto-attaches to federal claim.")
    if args.behavioral:
        lines.append(f"- LFP elasticity {args.lfp_elasticity} (Meyer-Rosenbaum 2001; range 0.3-0.7).")
        lines.append("- HoH scope includes single fathers (~10% of HoH per HI ACS S1101).")
        lines.append("- FICA + federal-tax savings linearly approximated using baseline values.")
        lines.append("- ±15% uncertainty band on behavioral persons-newly-poor from elasticity range.")
    if snap_offset_diag is not None:
        lines.append("- SNAP offset (1B) approximates per-filer gain by zeroing all affected "
                     "filers simultaneously; overstates offset slightly for multi-affected-filer HHs (rare).")
    if intensive_diag is not None:
        lines.append(f"- Intensive-margin response (1C) restricted to EITC phase-in workers, "
                     f"ε={args.intensive_elasticity_phase_in} (Eissa-Hoynes 2004 midpoint).")
    if args.bootstrap_replicates > 0:
        lines.append(f"- Bootstrap CI (1D) captures PUMS sampling uncertainty only; elasticity uncertainty via --lfp-elasticity sweep.")
    lines.append("- Other channels not modeled: marriage / fertility / take-up of other safety nets (TANF, UI).")

    summary = "\n".join(lines)
    (args.out / "summary.txt").write_text(summary + "\n")
    print(summary)
    LOG.info("Wrote outputs to %s", args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
