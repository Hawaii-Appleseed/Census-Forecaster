"""v4 phi-calibration ablation: does per-cell phi improve MAPE and coverage?

Trains calibration on anchors 2014–2020, evaluates on 2021–2022 (h=1,2,3).
Compares:
  - v3 baseline:  phi=DEFAULT_PHI (0.85) everywhere
  - v4 phi:       per-cell phi from MOE-derived noise/signal decomposition

Reports per-indicator MAPE delta and CI90 coverage. Acceptance bar:
  - Median MAPE drop >= 3% relative across indicators
  - No indicator/horizon cell coverage drops below 80%

Writes backtests/results/phi_ablation.md.

Usage
-----
    python -m lab.scripts.compare_phi_ablation
    python -m lab.scripts.compare_phi_ablation --train-end 2019
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train-end", type=int, default=2020,
                        help="Last anchor year in training set (default 2020)")
    parser.add_argument("--eval-start", type=int, default=2021,
                        help="First anchor year in evaluation set (default 2021)")
    parser.add_argument("--eval-end", type=int, default=2022,
                        help="Last anchor year in evaluation set (default 2022)")
    args = parser.parse_args(argv)

    print("[phi-ablation] loading panel ...", file=sys.stderr)
    from census_forecaster.acs.calibration import run_stratified_calibration
    from census_forecaster.acs.ensemble import project_ensemble_multi
    from census_forecaster.scripts.load_calibration_panel import PanelMissingError, load_panel

    try:
        series_by_key, populations, manifest = load_panel()
    except PanelMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    n_obs = sum(len(v) for v in series_by_key.values())
    print(
        f"[phi-ablation] panel: {n_obs} obs, {len(series_by_key)} series, "
        f"{len(populations)} counties",
        file=sys.stderr,
    )

    train_anchors = list(range(2014, args.train_end + 1))
    eval_anchors  = list(range(args.eval_start, args.eval_end + 1))
    horizons = (1, 2, 3)
    common = dict(
        series_by_key=series_by_key,
        populations=populations,
        horizons=horizons,
        as_of_mode="publication",
    )

    # Train on train_anchors to produce both calibrations
    print(
        f"[phi-ablation] training on anchors {train_anchors[0]}–{train_anchors[-1]} ...",
        file=sys.stderr,
    )
    cal_v3 = _calibrate_without_phi(common, train_anchors)
    cal_v4 = run_stratified_calibration(**common, anchor_years=train_anchors)

    phi_count = len((cal_v4.get("strata_records") or {}).get("phi", []))
    print(
        f"[phi-ablation] v4 phi records: {phi_count}  "
        f"(schema_version={cal_v4['schema_version']})",
        file=sys.stderr,
    )

    # Evaluate on eval_anchors
    print(
        f"[phi-ablation] evaluating on anchors {eval_anchors[0]}–{eval_anchors[-1]} ...",
        file=sys.stderr,
    )
    from census_forecaster.acs.projection import effective_year

    def _truncate(obs_sorted, anchor):
        return [o for o in obs_sorted if effective_year(o) <= anchor]

    rows_v3, rows_v4 = [], []
    for (geoid, indicator), full in series_by_key.items():
        full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
        for anchor in eval_anchors:
            for h in horizons:
                target = anchor + h
                actual_obs = next(
                    (o for o in full_sorted
                     if effective_year(o) == target and o.vintage == "1y"),
                    None,
                )
                if actual_obs is None or actual_obs.estimate <= 0:
                    continue
                train = _truncate(full_sorted, anchor)
                if len(train) < 3:
                    continue

                fp_v3 = project_ensemble_multi(
                    train, target_year=target, calibration=cal_v3,
                    populations=populations,
                )
                fp_v4 = project_ensemble_multi(
                    train, target_year=target, calibration=cal_v4,
                    populations=populations,
                )
                actual = actual_obs.estimate

                if fp_v3 is not None:
                    rows_v3.append({
                        "indicator": indicator, "geoid": geoid,
                        "anchor": anchor, "h": h,
                        "actual": actual, "point": fp_v3.point,
                        "ci_low": fp_v3.ci90_low, "ci_high": fp_v3.ci90_high,
                    })
                if fp_v4 is not None:
                    rows_v4.append({
                        "indicator": indicator, "geoid": geoid,
                        "anchor": anchor, "h": h,
                        "actual": actual, "point": fp_v4.point,
                        "ci_low": fp_v4.ci90_low, "ci_high": fp_v4.ci90_high,
                    })

    print(
        f"[phi-ablation] folds: v3={len(rows_v3)}, v4={len(rows_v4)}",
        file=sys.stderr,
    )

    report = _build_report(
        rows_v3, rows_v4,
        train_end=args.train_end,
        eval_start=args.eval_start,
        eval_end=args.eval_end,
        phi_count=phi_count,
    )

    out = Path("backtests/results/phi_ablation.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"[phi-ablation] wrote {out}", file=sys.stderr)
    print(report)
    return 0


# ---------------------------------------------------------------------------
# Calibration helper: build v3-equivalent calibration (phi stripped out)
# ---------------------------------------------------------------------------

def _calibrate_without_phi(common: dict, anchor_years: list) -> dict:
    """Run stratified calibration and strip phi records to simulate v3."""
    from census_forecaster.acs.calibration import run_stratified_calibration
    cal = run_stratified_calibration(**common, anchor_years=anchor_years)
    # Strip phi records so projection falls through to DEFAULT_PHI
    if "strata_records" in cal and "phi" in cal["strata_records"]:
        cal = {**cal, "strata_records": {
            k: v for k, v in cal["strata_records"].items() if k != "phi"
        }}
    if cal.get("schema_version") == 4:
        cal = {**cal, "schema_version": 3}
    return cal


# ---------------------------------------------------------------------------
# Report builder
# ---------------------------------------------------------------------------

def _mape_coverage(rows: list[dict]) -> dict[str, dict]:
    """Aggregate MAPE and CI90 coverage per indicator."""
    abs_err: dict[str, list[float]] = {}
    covered: dict[str, list[bool]] = {}
    for r in rows:
        ind = r["indicator"]
        actual = r["actual"]
        if actual <= 0:
            continue
        abs_err.setdefault(ind, []).append(abs(r["point"] - actual) / actual)
        covered.setdefault(ind, []).append(r["ci_low"] <= actual <= r["ci_high"])
    result = {}
    for ind in abs_err:
        errs = abs_err[ind]
        covs = covered.get(ind, [])
        result[ind] = {
            "mape": sum(errs) / len(errs) * 100,
            "coverage": sum(covs) / len(covs) * 100 if covs else 0.0,
            "n": len(errs),
        }
    return result


def _build_report(
    rows_v3: list[dict],
    rows_v4: list[dict],
    train_end: int,
    eval_start: int,
    eval_end: int,
    phi_count: int,
) -> str:
    v3 = _mape_coverage(rows_v3)
    v4 = _mape_coverage(rows_v4)
    all_indicators = sorted(set(v3) | set(v4))

    # Overall summary
    v3_mapes = [v3[i]["mape"] for i in all_indicators if i in v3]
    v4_mapes = [v4[i]["mape"] for i in all_indicators if i in v4]
    v3_med = _median(v3_mapes)
    v4_med = _median(v4_mapes)
    rel_delta = (v4_med - v3_med) / v3_med * 100 if v3_med else 0.0

    v3_covs = [v3[i]["coverage"] for i in all_indicators if i in v3]
    v4_covs = [v4[i]["coverage"] for i in all_indicators if i in v4]
    any_below_80_v4 = any(c < 80.0 for c in v4_covs)

    # Acceptance: median MAPE drop >= 3% relative, no coverage < 80%
    passed = rel_delta <= -3.0 and not any_below_80_v4

    lines = [
        "# Phi Ablation Results",
        "",
        f"Training anchors: 2014–{train_end}  |  Evaluation: {eval_start}–{eval_end}, h∈{{1,2,3}}",
        f"Phi records emitted: {phi_count}",
        "",
        "## Summary",
        "",
        "| Metric | v3 (φ=0.85) | v4 (per-cell φ) | Delta |",
        "|--------|------------|----------------|-------|",
        f"| Median MAPE | {v3_med:.2f}% | {v4_med:.2f}% | {rel_delta:+.1f}% |",
        f"| Any coverage < 80% | {'yes' if any(c < 80 for c in v3_covs) else 'no'} "
        f"| {'yes' if any_below_80_v4 else 'no'} | — |",
        "",
        f"**Acceptance bar:** median MAPE drop ≥ 3% relative + no coverage < 80%  →  "
        f"{'✅ PASS' if passed else '❌ FAIL'}",
        "",
        "## Per-indicator",
        "",
        "| Indicator | v3 MAPE | v4 MAPE | Δ MAPE | v3 Cov | v4 Cov | n |",
        "|-----------|---------|---------|--------|--------|--------|---|",
    ]

    for ind in all_indicators:
        r3 = v3.get(ind, {})
        r4 = v4.get(ind, {})
        m3 = r3.get("mape", float("nan"))
        m4 = r4.get("mape", float("nan"))
        c3 = r3.get("coverage", float("nan"))
        c4 = r4.get("coverage", float("nan"))
        n  = r3.get("n", r4.get("n", 0))
        delta = ((m4 - m3) / m3 * 100) if (r3 and r4 and m3) else float("nan")
        flag = " ⚠️" if c4 < 80.0 else ""
        lines.append(
            f"| {ind} | {m3:.2f}% | {m4:.2f}% | {delta:+.1f}% "
            f"| {c3:.1f}% | {c4:.1f}%{flag} | {n} |"
        )

    return "\n".join(lines) + "\n"


def _median(vals: list[float]) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    n = len(s)
    mid = n // 2
    return (s[mid - 1] + s[mid]) / 2.0 if n % 2 == 0 else s[mid]


if __name__ == "__main__":
    sys.exit(main())
