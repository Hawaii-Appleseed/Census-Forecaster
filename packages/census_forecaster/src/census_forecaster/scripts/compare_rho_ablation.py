"""Empirical member-correlation ablation (assessment item B7).

Arm A: v3 calibration with the v1 literal correlations
       (ρ_components=0.7, ρ_inner=0.7, ρ_anchor=0.5, cross-source 0.6,
       level-fusion 0.5).
Arm B: `enable_empirical_rho=True` — correlations estimated from the
       tuning folds (`acs.correlations`) and applied both inside the
       folds (trend_ensemble / multi_anchor SEs, hence κ) and in the
       blend.

Both arms include the ML member (production default). The ensemble is
synthesised from the bias+κ-corrected fold cache exactly as
`compare_ml_ablation` does, with the level-anchor fusion added.

Usage::

    python -m census_forecaster.scripts.compare_rho_ablation \\
        --output backtests/results/rho_empirical_ablation_YYYY-MM-DD.md
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

from ..acs.calibration import run_stratified_calibration
from ..acs.correlations import (
    ALL_PAIRS,
    LITERAL_RHO,
    PAYLOAD_KEY,
    build_rho_lookup,
    literal_rho_lookup,
)
from ..acs.strata import H_BUCKET_BOUNDS, WILDCARD, record_from_dict
from ._engine_ablation_common import (
    aggregate,
    build_ensemble_residuals,
    comparison_table,
    gate,
    pooled_rmse,
    verdict_lines,
)
from .compare_ml_ablation import _apply_v3_corrections, _residuals_from_calibration
from .load_calibration_panel import load_panel


def _rho_table(payload: dict) -> list[str]:
    recs = [record_from_dict(d) for d in payload.get(PAYLOAD_KEY, [])]
    by = {(r.indicator, r.method, r.h_bucket): r for r in recs}
    indicators = sorted({r.indicator for r in recs})
    lines = [
        "| Indicator | " + " | ".join(
            f"{p} (lit {LITERAL_RHO[p]:.1f})" for p in ALL_PAIRS
        ) + " |",
        "|---|" + "---:|" * len(ALL_PAIRS),
    ]
    for ind in indicators:
        cells = []
        for p in ALL_PAIRS:
            r = by.get((ind, p, WILDCARD))
            if r is None:
                cells.append("—")
            else:
                short = by.get((ind, p, "short"))
                long_ = by.get((ind, p, "long"))
                hb = ""
                if short is not None and long_ is not None:
                    hb = f" (s {short.value:.2f}/l {long_.value:.2f})"
                cells.append(f"{r.value:.2f} n={r.n_folds}{hb}")
        lines.append(f"| {ind} | " + " | ".join(cells) + " |")
    return lines


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--anchors", type=str, default="2014,2015,2016,2017,2018,2019,2020,2021,2022")
    p.add_argument("--horizons", type=str, default="1,2,3,4,5")
    p.add_argument("--no-ml", action="store_true", default=False,
                   help="Drop the ML member from both arms (faster; not the production blend).")
    args = p.parse_args(list(argv) if argv is not None else None)
    anchor_years = [int(s) for s in args.anchors.split(",")]
    horizons = [int(s) for s in args.horizons.split(",")]
    include_ml = not args.no_ml

    print("[rho-ablation] loading panel ...", file=sys.stderr)
    series, populations, _manifest = load_panel()

    print("[rho-ablation] arm A: literal ρ ...", file=sys.stderr)
    cal_a = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=include_ml, enable_empirical_rho=False,
    )
    print("[rho-ablation] arm B: empirical ρ ...", file=sys.stderr)
    cal_b = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=include_ml, enable_empirical_rho=True,
    )

    resid_a = _apply_v3_corrections(_residuals_from_calibration(cal_a), cal_a)
    resid_b = _apply_v3_corrections(_residuals_from_calibration(cal_b), cal_b)
    rho_b = build_rho_lookup(record_from_dict(d) for d in cal_b[PAYLOAD_KEY])

    ens_a = build_ensemble_residuals(resid_a, literal_rho_lookup, cal_a.get("rmse_by_indicator_method"))
    ens_b = build_ensemble_residuals(resid_b, rho_b, cal_b.get("rmse_by_indicator_method"))
    # Arm C: arm A folds (literal-ρ κ) blended at empirical ρ — isolates the
    # blend-level effect from the κ-recalibration effect.
    ens_c = build_ensemble_residuals(resid_a, rho_b, cal_a.get("rmse_by_indicator_method"))

    by_ind = lambda r: r.indicator  # noqa: E731
    by_hb = lambda r: r.h_bucket  # noqa: E731
    met_a = aggregate(ens_a, by_ind)
    met_b = aggregate(ens_b, by_ind)
    met_c = aggregate(ens_c, by_ind)
    g = gate(met_a, met_b, pooled_rmse(ens_a), pooled_rmse(ens_b))

    lines = [
        f"# Empirical member-correlation ablation ({cal_a['run_date']})",
        "",
        f"Anchors {anchor_years[0]}–{anchor_years[-1]}, h={horizons[0]}..{horizons[-1]}, "
        f"ML member {'on' if include_ml else 'off'}. Arm A = v1 literal ρ; "
        f"Arm B = `enable_empirical_rho=True` (folds + blend at empirical ρ); "
        f"Arm C = arm-A folds blended at arm-B ρ (blend-level effect only). "
        f"Metrics on the bias+κ-corrected ensemble synthesised from the fold cache "
        f"(`compare_ml_ablation` recipe + level-anchor fusion).",
        "",
        "## Estimated correlations (tuning folds, h-marginalised; short/long in parentheses)",
        "",
    ]
    lines += _rho_table(cal_b)
    lines += ["", "## Ensemble: literal (A) vs empirical (B)", ""]
    t, _i, _r, _v = comparison_table(met_a, met_b, "A", "B")
    lines += t
    lines += [
        "",
        f"Pooled ensemble RMSE: A {pooled_rmse(ens_a) * 100:.2f}% → B {pooled_rmse(ens_b) * 100:.2f}% "
        f"({g.pooled_delta * 100:+.2f}%); C {pooled_rmse(ens_c) * 100:.2f}%.",
        "",
        "## Ensemble: literal (A) vs blend-only empirical (C)",
        "",
    ]
    t, _i, _r, _v = comparison_table(met_a, met_c, "A", "C")
    lines += t
    lines += ["", "## Coverage by horizon bucket", "", "| h_bucket | Cov90 A | Cov90 B | Cov90 C | n |", "|---|---:|---:|---:|---:|"]
    ha, hb_, hc = aggregate(ens_a, by_hb), aggregate(ens_b, by_hb), aggregate(ens_c, by_hb)
    for hb in list(H_BUCKET_BOUNDS):
        a, b, c = ha.get(hb), hb_.get(hb), hc.get(hb)
        if a and b and c:
            lines.append(f"| {hb} | {a.coverage * 100:.1f}% | {b.coverage * 100:.1f}% | {c.coverage * 100:.1f}% | {b.n_folds} |")
    lines += ["", "## Member coverage (post-κ), A vs B", "", "| Member | Cov90 A | Cov90 B | n |", "|---|---:|---:|---:|"]
    ma = aggregate(resid_a, lambda r: r.method)
    mb = aggregate(resid_b, lambda r: r.method)
    for m in sorted(set(ma) & set(mb)):
        lines.append(f"| {m} | {ma[m].coverage * 100:.1f}% | {mb[m].coverage * 100:.1f}% | {mb[m].n_folds} |")
    lines += [""] + verdict_lines(g, "enable_empirical_rho")
    report = "\n".join(lines)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
        print(f"[rho-ablation] wrote {args.output}", file=sys.stderr)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
