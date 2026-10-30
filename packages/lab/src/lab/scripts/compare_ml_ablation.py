"""ML ablation: does ml_trend earn its keep on the calibration panel?

Runs the v3 stratified calibration twice — once with ``include_ml=False``
and once with ``include_ml=True`` — and compares per-indicator RMSE-pct
and CI90 coverage on a held-out walk-forward. Decides the ship gate:

    Ship if and only if:
      1. ensemble_with_ml RMSE-pct drops ≥ 5% relative to ensemble_v3
         on ≥ 12 of 16 indicators (75%).
      2. CI90 coverage stays inside [85%, 95%] for every indicator under
         ensemble_with_ml.
      3. No indicator's RMSE regresses by > 2% absolute.

Usage::

    python -m lab.scripts.compare_ml_ablation
    python -m lab.scripts.compare_ml_ablation --output report.md

The two calibration runs together take ~6–8 minutes on the bundled
panel (16 indicators × 9 anchors × 5 horizons; the ML half adds ~5 min
of HGB training).
"""
from __future__ import annotations

import argparse
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from census_forecaster.acs.calibration import (
    COVERAGE_LOWER_BOUND,
    COVERAGE_UPPER_BOUND,
    FoldResidual,
    run_stratified_calibration,
)
from census_forecaster.acs.ml_features import build_panel_index, load_county_data
from census_forecaster.acs.ml_trend import (
    METHOD_NAME as ML_METHOD,
    project_ml_trend,
    train_ml_model,
)
from census_forecaster.acs.projection import effective_year
from census_forecaster.scripts.load_calibration_panel import load_panel


# Ship-gate thresholds. See module docstring.
SHIP_GATE_RMSE_DELTA = 0.05         # 5% relative RMSE drop required
SHIP_GATE_REGRESSION_LIMIT = 0.02   # no indicator may regress by > 2% absolute
SHIP_GATE_MIN_INDICATORS_IMPROVED_FRACTION = 0.75  # ≥ 75% of indicators must improve


@dataclass
class IndicatorMetrics:
    """Per-indicator aggregate metrics from one calibration run."""
    indicator: str
    rmse_pct: float
    coverage: float
    n_folds: int


def _aggregate_per_indicator(
    fold_residuals: Sequence[FoldResidual],
    method: str,
) -> dict[str, IndicatorMetrics]:
    """Collapse FoldResidual list to (indicator → metrics) for one method."""
    by_ind: dict[str, list[FoldResidual]] = defaultdict(list)
    for r in fold_residuals:
        if r.method == method and r.actual > 0:
            by_ind[r.indicator].append(r)

    out: dict[str, IndicatorMetrics] = {}
    for ind, items in by_ind.items():
        sq = [((r.point - r.actual) / r.actual) ** 2 for r in items]
        cov = [1 if r.ci90_low <= r.actual <= r.ci90_high else 0 for r in items]
        rmse = math.sqrt(sum(sq) / len(sq)) if sq else float("nan")
        coverage = sum(cov) / len(cov) if cov else float("nan")
        out[ind] = IndicatorMetrics(
            indicator=ind, rmse_pct=rmse, coverage=coverage, n_folds=len(items),
        )
    return out


def _build_with_ml_residuals(
    fold_residuals: Sequence[FoldResidual],
    series_by_key: dict,
    populations: dict,
    rho_inner: float = 0.7,
    rho_anchor: float = 0.5,
) -> list[FoldResidual]:
    """Synthesise the ``ensemble_with_ml`` residuals from cached per-method residuals.

    The ensemble combiner is inverse-variance Bates-Granger: at each
    (geoid, anchor, h) we read off (point, se_total) for trend_ensemble,
    multi_anchor, and ml_trend (when available), then combine in two
    stages:

    1. Inner (target-side): trend_ensemble + ml_trend at ρ=0.7.
    2. Outer (target vs anchor): inner + multi_anchor at ρ=0.5.

    Weights derive from inverse variance of the post-correction residuals,
    which are what's already in the FoldResidual cache. Bias correction
    has already been applied upstream by ``run_stratified_calibration``;
    we don't re-apply it here.

    Returns one synthesised FoldResidual per (geoid, anchor, h) where at
    least the trend ensemble was buildable; flagged with method
    ``"ensemble_with_ml"``.
    """
    by_key: dict[tuple[str, str, int, int], dict[str, FoldResidual]] = defaultdict(dict)
    for r in fold_residuals:
        key = (r.indicator, r.geoid, r.anchor_year, r.horizon)
        by_key[key][r.method] = r

    out: list[FoldResidual] = []
    for key, methods in by_key.items():
        trend = methods.get("trend_ensemble")
        anchor = methods.get("multi_anchor")
        ml = methods.get(ML_METHOD)
        # We need at least one component beyond raw passthrough.
        components = [m for m in (trend, ml) if m is not None]
        if not components and anchor is None:
            continue
        # Inner: trend + ml at ρ=rho_inner (default 0.7).
        if trend is not None and ml is not None:
            inner = _combine_two_residuals(trend, ml, rho=rho_inner, label="target_ensemble")
        elif trend is not None:
            inner = trend
        elif ml is not None:
            inner = ml
        else:
            inner = None
        # Outer: inner + anchor at ρ=rho_anchor (default 0.5).
        if inner is not None and anchor is not None:
            ens = _combine_two_residuals(inner, anchor, rho=rho_anchor, label="ensemble_with_ml")
        elif inner is not None:
            ens = _replace_method(inner, "ensemble_with_ml")
        elif anchor is not None:
            ens = _replace_method(anchor, "ensemble_with_ml")
        else:
            continue
        out.append(ens)
    return out


def _combine_two_residuals(
    a: FoldResidual,
    b: FoldResidual,
    rho: float,
    label: str,
) -> FoldResidual:
    """Inverse-variance combination of two FoldResidual rows at correlation rho."""
    inv_a = 1.0 / (a.se_total ** 2) if (math.isfinite(a.se_total) and a.se_total > 0) else 0.0
    inv_b = 1.0 / (b.se_total ** 2) if (math.isfinite(b.se_total) and b.se_total > 0) else 0.0
    total = inv_a + inv_b
    if total <= 0:
        wa, wb = 0.5, 0.5
    else:
        wa, wb = inv_a / total, inv_b / total
    point = wa * a.point + wb * b.point
    var = (
        (wa * a.se_total) ** 2
        + (wb * b.se_total) ** 2
        + 2.0 * rho * wa * wb * a.se_total * b.se_total
    )
    se_total = math.sqrt(var) if var > 0 else 0.0
    se_sample = math.sqrt((wa * a.se_sample) ** 2 + (wb * b.se_sample) ** 2)
    se_forecast = math.sqrt((wa * a.se_forecast) ** 2 + (wb * b.se_forecast) ** 2)
    z = 1.645
    ci_lo = point - z * se_total
    ci_hi = point + z * se_total
    return FoldResidual(
        indicator=a.indicator,
        method=label,
        geoid=a.geoid,
        anchor_year=a.anchor_year,
        horizon=a.horizon,
        pop_bucket=a.pop_bucket,
        h_bucket=a.h_bucket,
        actual=a.actual,
        point=point,
        se_total=se_total,
        se_sample=se_sample,
        se_forecast=se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
    )


def _replace_method(r: FoldResidual, method: str) -> FoldResidual:
    return FoldResidual(
        indicator=r.indicator, method=method, geoid=r.geoid,
        anchor_year=r.anchor_year, horizon=r.horizon,
        pop_bucket=r.pop_bucket, h_bucket=r.h_bucket,
        actual=r.actual, point=r.point, se_total=r.se_total,
        se_sample=r.se_sample, se_forecast=r.se_forecast,
        ci90_low=r.ci90_low, ci90_high=r.ci90_high,
    )


def _residuals_from_calibration(payload: dict) -> list[FoldResidual]:
    """Pull cached fold_residuals dicts back into FoldResidual dataclasses."""
    out: list[FoldResidual] = []
    for d in payload.get("fold_residuals", []):
        # The on-payload entry may not carry se_sample/se_forecast (the
        # writer drops a few fields for compactness). Re-derive missing
        # SE components from se_total when needed.
        out.append(FoldResidual(
            indicator=d["indicator"],
            method=d["method"],
            geoid=d["geoid"],
            anchor_year=d["anchor_year"],
            horizon=d["horizon"],
            pop_bucket=d["pop_bucket"],
            h_bucket=d["h_bucket"],
            actual=d["actual"],
            point=d["point"],
            se_total=d["se_total"],
            se_sample=d.get("se_sample", 0.0),
            se_forecast=d.get("se_forecast", d["se_total"]),
            ci90_low=d["ci90_low"],
            ci90_high=d["ci90_high"],
        ))
    return out


def _apply_v3_corrections(
    fold_residuals: Sequence[FoldResidual],
    calibration_payload: dict,
) -> list[FoldResidual]:
    """Apply v3 bias + κ corrections to a list of fold residuals in memory.

    Mirrors what ``ensemble._apply_bias_correction`` and
    ``ensemble._apply_se_override`` do at projection time, but operates
    on the cached residual list so the ablation report measures coverage
    on the production-ready CIs rather than the raw (uncalibrated)
    output of the projection step.
    """
    from census_forecaster.acs.strata import index_records, record_from_dict, DEFAULT_N_THRESHOLD
    from census_forecaster.acs.projection import EMPIRICAL_SE_INFLATOR

    sr = calibration_payload.get("strata_records") or {}
    bias_recs = [record_from_dict(d) for d in sr.get("bias", [])]
    se_recs = [record_from_dict(d) for d in sr.get("se_inflator", [])]
    bias_lookup = index_records(
        records=bias_recs, n_threshold=DEFAULT_N_THRESHOLD, floor_value=0.0,
    )
    se_lookup = index_records(
        records=se_recs, n_threshold=DEFAULT_N_THRESHOLD,
        floor_value=EMPIRICAL_SE_INFLATOR,
    )

    out: list[FoldResidual] = []
    for r in fold_residuals:
        # Bias correction: point / exp(b), CI/SE scale by exp(-b).
        b_rec, _ = bias_lookup(r.indicator, r.method, r.pop_bucket, r.h_bucket)
        b = b_rec.value
        bias_factor = math.exp(-b) if (b != 0.0 and math.isfinite(b)) else 1.0
        point = r.point * bias_factor
        se_total = r.se_total * bias_factor
        se_sample = r.se_sample * bias_factor
        se_forecast = r.se_forecast * bias_factor
        ci_lo = r.ci90_low * bias_factor
        ci_hi = r.ci90_high * bias_factor

        # SE override: κ_strata / EMPIRICAL_SE_INFLATOR multiplier.
        se_rec, _ = se_lookup(r.indicator, r.method, r.pop_bucket, r.h_bucket)
        kappa = se_rec.value
        if math.isfinite(kappa) and kappa > 0:
            factor = kappa / EMPIRICAL_SE_INFLATOR
            new_se_total = max(se_total * factor, se_sample)
            new_forecast_var = max(new_se_total ** 2 - se_sample ** 2, 0.0)
            se_forecast = math.sqrt(new_forecast_var)
            se_total = new_se_total
            z = 1.645
            ci_lo = point - z * se_total
            ci_hi = point + z * se_total

        out.append(FoldResidual(
            indicator=r.indicator,
            method=r.method,
            geoid=r.geoid,
            anchor_year=r.anchor_year,
            horizon=r.horizon,
            pop_bucket=r.pop_bucket,
            h_bucket=r.h_bucket,
            actual=r.actual,
            point=point,
            se_total=se_total,
            se_sample=se_sample,
            se_forecast=se_forecast,
            ci90_low=ci_lo,
            ci90_high=ci_hi,
        ))
    return out


# -----------------------------------------------------------------------------
# Reporting
# -----------------------------------------------------------------------------

def format_report(
    no_ml_metrics: dict[str, IndicatorMetrics],
    with_ml_metrics: dict[str, IndicatorMetrics],
    ml_only_metrics: dict[str, IndicatorMetrics],
    ml_no_bps_metrics: dict[str, IndicatorMetrics] | None = None,
) -> str:
    """Build the Markdown report comparing calibrations."""
    indicators = sorted(set(no_ml_metrics) | set(with_ml_metrics))
    lines: list[str] = []
    bps_col = ml_no_bps_metrics is not None
    lines.append("# ML ablation: ensemble_with_ml (+ BPS) vs ensemble_v3")
    lines.append("")
    header = (
        "| Indicator | RMSE no-ml | RMSE ml+BPS | Δ (rel) | "
        + ("RMSE ml-noBPS | BPS Δ (rel) | " if bps_col else "")
        + "Cov90 no-ml | Cov90 ml+BPS | n folds |"
    )
    sep = "|---|---:|---:|---:|" + ("|---:|---:|" if bps_col else "") + "---:|---:|---:|"
    lines.append(header)
    lines.append(sep)
    improved = 0
    regressed = 0
    n_total = 0
    coverage_violations: list[str] = []
    for ind in indicators:
        a = no_ml_metrics.get(ind)
        b = with_ml_metrics.get(ind)
        if a is None or b is None:
            continue
        n_total += 1
        rel = (b.rmse_pct - a.rmse_pct) / a.rmse_pct if a.rmse_pct > 0 else float("nan")
        if rel <= -SHIP_GATE_RMSE_DELTA:
            improved += 1
        if rel >= SHIP_GATE_REGRESSION_LIMIT:
            regressed += 1
        if not (COVERAGE_LOWER_BOUND <= b.coverage <= COVERAGE_UPPER_BOUND):
            coverage_violations.append(
                f"{ind}: cov={b.coverage * 100:.1f}% (target [85, 95])"
            )
        bps_extra = ""
        if bps_col and ml_no_bps_metrics is not None:
            c = ml_no_bps_metrics.get(ind)
            if c is not None:
                bps_delta = (b.rmse_pct - c.rmse_pct) / c.rmse_pct if c.rmse_pct > 0 else float("nan")
                bps_extra = f" {c.rmse_pct * 100:.2f}% | {bps_delta * 100:+.2f}% |"
            else:
                bps_extra = " — | — |"
        lines.append(
            f"| {ind} | {a.rmse_pct * 100:.2f}% | {b.rmse_pct * 100:.2f}% | "
            f"{rel * 100:+.2f}% |{bps_extra} {a.coverage * 100:.1f}% | "
            f"{b.coverage * 100:.1f}% | {b.n_folds} |"
        )
    lines.append("")
    lines.append("## ml_trend stand-alone metrics")
    lines.append("")
    lines.append("| Indicator | RMSE-pct | Cov90 | n folds |")
    lines.append("|---|---:|---:|---:|")
    for ind in sorted(ml_only_metrics):
        m = ml_only_metrics[ind]
        lines.append(
            f"| {ind} | {m.rmse_pct * 100:.2f}% | {m.coverage * 100:.1f}% | {m.n_folds} |"
        )
    lines.append("")
    lines.append("## Ship gate verdict")
    lines.append("")
    fraction_improved = improved / n_total if n_total > 0 else 0.0
    rule1 = fraction_improved >= SHIP_GATE_MIN_INDICATORS_IMPROVED_FRACTION
    rule2 = not coverage_violations
    rule3 = regressed == 0
    lines.append(
        f"* Rule 1 (≥{SHIP_GATE_MIN_INDICATORS_IMPROVED_FRACTION * 100:.0f}% indicators "
        f"improve RMSE by ≥{SHIP_GATE_RMSE_DELTA * 100:.0f}%): "
        f"**{'PASS' if rule1 else 'FAIL'}** "
        f"({improved}/{n_total} = {fraction_improved * 100:.1f}%)"
    )
    lines.append(
        f"* Rule 2 (CI90 coverage ∈ [85%, 95%] for all indicators): "
        f"**{'PASS' if rule2 else 'FAIL'}**"
    )
    if coverage_violations:
        for v in coverage_violations:
            lines.append(f"  * {v}")
    lines.append(
        f"* Rule 3 (no indicator regresses by > "
        f"{SHIP_GATE_REGRESSION_LIMIT * 100:.0f}% RMSE): "
        f"**{'PASS' if rule3 else 'FAIL'}** "
        f"({regressed} indicator(s) regressed)"
    )
    lines.append("")
    if rule1 and rule2 and rule3:
        lines.append("## **Verdict: SHIP** — flip `use_ml=True` default.")
    else:
        lines.append(
            "## **Verdict: HOLD** — keep `use_ml=False` default; ship as opt-in only."
        )
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

def main(argv: Iterable[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--output", type=Path,
        default=None,
        help="Write Markdown report to this path; if omitted, print to stdout",
    )
    p.add_argument(
        "--anchors", type=str,
        default="2014,2015,2016,2017,2018,2019,2020,2021,2022",
        help="Comma-separated anchor years (default: 2014..2022)",
    )
    p.add_argument(
        "--horizons", type=str, default="1,2,3,4,5",
        help="Comma-separated horizons in years (default: 1..5)",
    )
    p.add_argument(
        "--rho-sweep", action="store_true", default=False,
        help=(
            "Append a ρ_inner ∈ {0.5..0.9} sweep of ensemble_with_ml CI90 "
            "coverage (METHODOLOGY §'Why opt-in' condition 2)."
        ),
    )
    args = p.parse_args(list(argv) if argv is not None else None)

    anchor_years = [int(s) for s in args.anchors.split(",")]
    horizons = [int(s) for s in args.horizons.split(",")]

    print(f"[ablation] loading panel ...", file=sys.stderr)
    series, populations, manifest = load_panel()
    print(
        f"[ablation] panel: {sum(len(v) for v in series.values())} obs, "
        f"{len(series)} (geoid, indicator) series, {len(populations)} counties",
        file=sys.stderr,
    )

    county = load_county_data()
    county_no_bps = {k: v for k, v in county.items() if k != "bps"}
    n_ind = len(set(ind for (_, ind) in series))
    n_models = n_ind * len(anchor_years)

    print(f"[ablation] running v3 calibration WITHOUT ml ...", file=sys.stderr)
    cal_no_ml = run_stratified_calibration(
        series_by_key=series,
        anchor_years=anchor_years,
        horizons=horizons,
        populations=populations,
        include_ml=False,
    )

    print(
        f"[ablation] running v3 calibration WITH ml, NO BPS "
        f"(trains {n_models} HGB models) ...",
        file=sys.stderr,
    )
    cal_ml_no_bps = run_stratified_calibration(
        series_by_key=series,
        anchor_years=anchor_years,
        horizons=horizons,
        populations=populations,
        include_ml=True,
        county_data=county_no_bps,   # registry minus bps → no BPS features
    )

    print(
        f"[ablation] running v3 calibration WITH ml, WITH BPS "
        f"(trains {n_models} HGB models) ...",
        file=sys.stderr,
    )
    cal_ml_bps = run_stratified_calibration(
        series_by_key=series,
        anchor_years=anchor_years,
        horizons=horizons,
        populations=populations,
        include_ml=True,
        county_data=county,
    )

    # Apply v3 bias + κ corrections to each method's residuals.
    print(f"[ablation] applying v3 corrections to fold residuals ...", file=sys.stderr)
    resid_no_ml = _apply_v3_corrections(_residuals_from_calibration(cal_no_ml), cal_no_ml)
    resid_ml_no_bps = _apply_v3_corrections(_residuals_from_calibration(cal_ml_no_bps), cal_ml_no_bps)
    resid_ml_bps = _apply_v3_corrections(_residuals_from_calibration(cal_ml_bps), cal_ml_bps)

    print(f"[ablation] aggregating per-indicator metrics ...", file=sys.stderr)
    ensemble_v3 = _build_ensemble_v3_residuals(resid_no_ml)
    ensemble_ml_no_bps = _build_with_ml_residuals(resid_ml_no_bps, series, populations)
    ensemble_ml_bps = _build_with_ml_residuals(resid_ml_bps, series, populations)

    no_ml_metrics = _aggregate_per_indicator(ensemble_v3, "ensemble_v3")
    ml_no_bps_metrics = _aggregate_per_indicator(ensemble_ml_no_bps, "ensemble_with_ml")
    ml_bps_metrics = _aggregate_per_indicator(ensemble_ml_bps, "ensemble_with_ml")
    ml_only_metrics = _aggregate_per_indicator(resid_ml_bps, ML_METHOD)

    report = format_report(no_ml_metrics, ml_bps_metrics, ml_only_metrics,
                           ml_no_bps_metrics=ml_no_bps_metrics)

    if args.rho_sweep:
        # METHODOLOGY §"Why opt-in" condition (2): a ρ sweep showing the
        # combined ensemble's coverage stays uniformly inside [85%, 95%].
        # Cheap: re-synthesises the blend from the cached corrected
        # residuals at each ρ_inner — no re-training, no re-projection.
        rho_lines = [
            "",
            "## ρ_inner sweep — ensemble_with_ml CI90 coverage by indicator",
            "",
            "Coverage of the trend+ML inner blend correlation. ρ_anchor "
            "stays 0.5 throughout. In-band = [85%, 95%].",
            "",
        ]
        rhos = [0.5, 0.6, 0.7, 0.8, 0.9]
        per_rho: dict[float, dict[str, IndicatorMetrics]] = {}
        for rho in rhos:
            ens = _build_with_ml_residuals(
                resid_ml_bps, series, populations, rho_inner=rho,
            )
            per_rho[rho] = _aggregate_per_indicator(ens, "ensemble_with_ml")
        indicators = sorted(set().union(*[m.keys() for m in per_rho.values()]))
        rho_lines.append(
            "| Indicator | " + " | ".join(f"ρ={r:.1f}" for r in rhos) + " |"
        )
        rho_lines.append("|---|" + "---:|" * len(rhos))
        for ind in indicators:
            cells = []
            for rho in rhos:
                m = per_rho[rho].get(ind)
                if m is None:
                    cells.append("—")
                else:
                    flag = "" if COVERAGE_LOWER_BOUND <= m.coverage <= COVERAGE_UPPER_BOUND else " ⚠"
                    cells.append(f"{m.coverage * 100:.1f}%{flag}")
            rho_lines.append(f"| {ind} | " + " | ".join(cells) + " |")
        rho_lines.append("")
        for rho in rhos:
            n_in_band = sum(
                1 for m in per_rho[rho].values()
                if COVERAGE_LOWER_BOUND <= m.coverage <= COVERAGE_UPPER_BOUND
            )
            rho_lines.append(
                f"* ρ={rho:.1f}: {n_in_band}/{len(per_rho[rho])} indicators in band"
            )
        report = report + "\n" + "\n".join(rho_lines)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
        print(f"[ablation] wrote {args.output}", file=sys.stderr)
    print(report)
    return 0


def _build_ensemble_v3_residuals(
    fold_residuals: Sequence[FoldResidual],
) -> list[FoldResidual]:
    """Build the trend_ensemble + multi_anchor v3 ensemble (no ML)."""
    by_key: dict[tuple[str, str, int, int], dict[str, FoldResidual]] = defaultdict(dict)
    for r in fold_residuals:
        key = (r.indicator, r.geoid, r.anchor_year, r.horizon)
        by_key[key][r.method] = r

    out: list[FoldResidual] = []
    for key, methods in by_key.items():
        trend = methods.get("trend_ensemble")
        anchor = methods.get("multi_anchor")
        if trend is not None and anchor is not None:
            ens = _combine_two_residuals(trend, anchor, rho=0.5, label="ensemble_v3")
        elif trend is not None:
            ens = _replace_method(trend, "ensemble_v3")
        elif anchor is not None:
            ens = _replace_method(anchor, "ensemble_v3")
        else:
            continue
        out.append(ens)
    return out


if __name__ == "__main__":
    sys.exit(main())
