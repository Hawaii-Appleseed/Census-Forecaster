"""MOE-weighted trend/AR(1) fit ablation (assessment item B3).

Arm A: v3 calibration with equal-weight member fits (production).
Arm B: `moe_weighted=True` — damped-trend and AR(1) members fit with
       per-observation sampling-precision weights
       (`projection.fit_damped_trend(moe_weighted=True)`).

Both arms include the ML member (production default) and use the
generator's member-correlation default (`--literal-rho` forces the v1
literals, the configuration of the 2026-10-09 report). The hypothesis is that gains concentrate in the
`small` population bucket, so the report stratifies by pop bucket for
both the ensemble and the trend_ensemble member alone.

Usage::

    python -m census_forecaster.scripts.compare_moe_weighted_ablation \\
        --output backtests/results/moe_weighted_ablation_YYYY-MM-DD.md
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable
from pathlib import Path

from ..acs.calibration import run_stratified_calibration
from ..acs.correlations import PAYLOAD_KEY, build_rho_lookup, literal_rho_lookup
from ..acs.strata import POP_BUCKET_BOUNDS, record_from_dict
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


def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--anchors", type=str, default="2014,2015,2016,2017,2018,2019,2020,2021,2022")
    p.add_argument("--horizons", type=str, default="1,2,3,4,5")
    p.add_argument("--no-ml", action="store_true", default=False,
                   help="Drop the ML member from both arms (faster; not the production blend).")
    p.add_argument("--literal-rho", action="store_true", default=False,
                   help="Build both arms under the v1 literal member correlations.")
    args = p.parse_args(list(argv) if argv is not None else None)
    anchor_years = [int(s) for s in args.anchors.split(",")]
    horizons = [int(s) for s in args.horizons.split(",")]
    include_ml = not args.no_ml

    print("[moe-ablation] loading panel ...", file=sys.stderr)
    series, populations, _manifest = load_panel()

    print("[moe-ablation] arm A: equal-weight fits ...", file=sys.stderr)
    cal_a = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=include_ml,
        enable_empirical_rho=not args.literal_rho,
    )
    print("[moe-ablation] arm B: moe_weighted fits ...", file=sys.stderr)
    cal_b = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=include_ml, moe_weighted=True,
        enable_empirical_rho=not args.literal_rho,
    )

    resid_a = _apply_v3_corrections(_residuals_from_calibration(cal_a), cal_a)
    resid_b = _apply_v3_corrections(_residuals_from_calibration(cal_b), cal_b)
    if args.literal_rho:
        rho_a = rho_b = literal_rho_lookup
    else:
        rho_a = build_rho_lookup(record_from_dict(d) for d in cal_a[PAYLOAD_KEY])
        rho_b = build_rho_lookup(record_from_dict(d) for d in cal_b[PAYLOAD_KEY])
    ens_a = build_ensemble_residuals(resid_a, rho_a, cal_a.get("rmse_by_indicator_method"))
    ens_b = build_ensemble_residuals(resid_b, rho_b, cal_b.get("rmse_by_indicator_method"))

    by_ind = lambda r: r.indicator  # noqa: E731
    by_pop = lambda r: r.pop_bucket  # noqa: E731
    by_ind_pop = lambda r: f"{r.indicator} × {r.pop_bucket}"  # noqa: E731
    met_a, met_b = aggregate(ens_a, by_ind), aggregate(ens_b, by_ind)
    g = gate(met_a, met_b, pooled_rmse(ens_a), pooled_rmse(ens_b))

    pop_order = [name for name, _lo, _hi in POP_BUCKET_BOUNDS]

    def _pop_rows(a: dict, b: dict) -> list[str]:
        rows = ["| pop_bucket | RMSE A | RMSE B | Δ (rel) | Cov90 A | Cov90 B | n |", "|---|---:|---:|---:|---:|---:|---:|"]
        for pb in pop_order + ["*"]:
            x, y = a.get(pb), b.get(pb)
            if x is None or y is None:
                continue
            rel = (y.rmse_pct - x.rmse_pct) / x.rmse_pct if x.rmse_pct > 0 else float("nan")
            rows.append(
                f"| {pb} | {x.rmse_pct * 100:.2f}% | {y.rmse_pct * 100:.2f}% | {rel * 100:+.2f}% | "
                f"{x.coverage * 100:.1f}% | {y.coverage * 100:.1f}% | {y.n_folds} |"
            )
        return rows

    lines = [
        f"# MOE-weighted trend/AR(1) fit ablation ({cal_a['run_date']})",
        "",
        f"Anchors {anchor_years[0]}–{anchor_years[-1]}, h={horizons[0]}..{horizons[-1]}, "
        f"ML member {'on' if include_ml else 'off'}, member correlations "
        f"{'v1 literal' if args.literal_rho else 'empirical'}. Arm A = equal-weight fits; "
        f"Arm B = `moe_weighted=True` (weights clamp(R̄/R_t) ∈ [0.25, 4], R_t = SE_log²). "
        f"Metrics on the bias+κ-corrected ensemble synthesised from the fold cache "
        f"(`compare_ml_ablation` recipe + level-anchor fusion).",
        "",
        "## Ensemble by indicator",
        "",
    ]
    t, _i, _r, _v = comparison_table(met_a, met_b, "A", "B")
    lines += t
    lines += [
        "",
        f"Pooled ensemble RMSE: A {pooled_rmse(ens_a) * 100:.2f}% → B {pooled_rmse(ens_b) * 100:.2f}% "
        f"({g.pooled_delta * 100:+.2f}%).",
        "",
        "## Ensemble by population bucket",
        "",
    ]
    lines += _pop_rows(aggregate(ens_a, by_pop), aggregate(ens_b, by_pop))
    lines += ["", "## trend_ensemble member alone, by population bucket (post-κ)", ""]
    lines += _pop_rows(
        aggregate(resid_a, by_pop, method="trend_ensemble"),
        aggregate(resid_b, by_pop, method="trend_ensemble"),
    )
    lines += ["", "## trend_ensemble member alone, by indicator (post-κ)", ""]
    t, _i, _r, _v = comparison_table(
        aggregate(resid_a, by_ind, method="trend_ensemble"),
        aggregate(resid_b, by_ind, method="trend_ensemble"), "A", "B",
    )
    lines += t
    lines += ["", "## Ensemble, `small` bucket by indicator", ""]
    sa = {k: v for k, v in aggregate(ens_a, by_ind_pop).items() if k.endswith("× small")}
    sb = {k: v for k, v in aggregate(ens_b, by_ind_pop).items() if k.endswith("× small")}
    t, _i, _r, _v = comparison_table(sa, sb, "A", "B", key_label="Indicator × pop")
    lines += t
    lines += [""] + verdict_lines(g, "moe_weighted")
    report = "\n".join(lines)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
        print(f"[moe-ablation] wrote {args.output}", file=sys.stderr)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
