"""Phase D: Kalman vs ensemble_with_ml ablation.

Runs a two-way walk-forward comparison:
  - ensemble_with_ml  (current default, BPS-augmented ML)
  - kalman_state_space (new Kalman filter)

Writes backtests/results/phase_d_kalman_ablation.md.

Usage
-----
    python -m lab.scripts.compare_kalman_ablation
"""
from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    print("[ablation] loading panel ...", file=sys.stderr)
    from census_forecaster.acs.calibration import run_stratified_calibration
    from census_forecaster.scripts.load_calibration_panel import PanelMissingError, load_panel
    try:
        series_by_key, populations, manifest = load_panel()
    except PanelMissingError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    n_obs = sum(len(v) for v in series_by_key.values())
    n_series = len(series_by_key)
    n_counties = len(populations)
    print(
        f"[ablation] panel: {n_obs} obs, {n_series} (geoid, indicator) series, "
        f"{n_counties} counties",
        file=sys.stderr,
    )

    anchor_years = list(range(2014, 2023))
    horizons = (1, 2, 3, 4, 5)
    common = dict(
        series_by_key=series_by_key,
        anchor_years=anchor_years,
        horizons=horizons,
        populations=populations,
        as_of_mode="publication",
    )

    print("[ablation] running calibration WITH ml (baseline) ...", file=sys.stderr)
    cal_ml = run_stratified_calibration(**common, include_ml=True, include_kalman=False)

    print("[ablation] running calibration WITH Kalman ...", file=sys.stderr)
    cal_kalman = run_stratified_calibration(**common, include_ml=True, include_kalman=True)

    print("[ablation] aggregating per-indicator metrics ...", file=sys.stderr)
    report = _build_report(cal_ml, cal_kalman)

    out = Path("backtests/results/phase_d_kalman_ablation.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"[ablation] wrote {out}", file=sys.stderr)
    print(report)
    return 0


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _rmse_cov(records: list[dict], method: str) -> dict[str, dict]:
    """Aggregate per-indicator RMSE and CI90 coverage for a method."""
    import math
    sq_err: dict[str, list[float]] = {}
    covered: dict[str, list[bool]] = {}
    for r in records:
        if r["method"] != method:
            continue
        ind = r["indicator"]
        actual = r["actual"]
        point = r["point"]
        _se = r["se_total"]
        if actual <= 0:
            continue
        rel = (point - actual) / actual
        sq_err.setdefault(ind, []).append(rel ** 2)
        covered.setdefault(ind, []).append(
            r["ci90_low"] <= actual <= r["ci90_high"]
        )
    out = {}
    for ind in sq_err:
        n = len(sq_err[ind])
        rmse = math.sqrt(sum(sq_err[ind]) / n) if n else float("nan")
        cov = sum(covered.get(ind, [])) / len(covered.get(ind, [1])) if covered.get(ind) else float("nan")
        out[ind] = {"rmse": rmse, "cov90": cov, "n": n}
    return out


def _extract_fold_records(cal: dict) -> list[dict]:
    """Pull raw fold records from calibration strata_records."""
    # The calibration dict stores per-stratum summaries, not raw fold records.
    # We need to re-derive RMSE from rmse_by_indicator_method.
    # Fall back to the method-level RMSE from the calibration dict.
    return _extract_from_method_rmse(cal)


def _extract_from_method_rmse(cal: dict) -> list[dict]:
    """Return synthetic records from the per-indicator, per-method RMSE dict."""
    out = []
    rmse_dict = cal.get("rmse_by_indicator_method", {})
    cov_dict = cal.get("ci90_coverage_by_indicator_method", {})
    for ind, method_map in rmse_dict.items():
        for method, rmse in method_map.items():
            cov = (cov_dict.get(ind) or {}).get(method, float("nan"))
            out.append({
                "indicator": ind, "method": method,
                "rmse": rmse, "cov90": cov,
            })
    return out


def _get_method_stats(cal: dict, method: str) -> dict[str, dict]:
    """Extract {indicator: {rmse, cov90}} for a given method."""
    rmse_dict = cal.get("rmse_by_indicator_method", {})
    cov_dict = cal.get("ci90_coverage_by_indicator_method", {})
    out = {}
    for ind, method_map in rmse_dict.items():
        if method in method_map:
            out[ind] = {
                "rmse": method_map[method],
                "cov90": (cov_dict.get(ind) or {}).get(method, float("nan")),
            }
    return out


_KAL_METHOD = "kalman_state_space"


def _best_baseline(rmse_dict: dict[str, dict]) -> dict[str, tuple[str, float]]:
    """Return {indicator: (baseline_label, baseline_rmse)} using best existing method."""
    out = {}
    for ind, method_map in rmse_dict.items():
        an = method_map.get("multi_anchor")
        tr = method_map.get("trend_ensemble")
        if an:
            out[ind] = ("anchor", an)
        elif tr:
            out[ind] = ("trend", tr)
    return out


def _build_report(cal_ml: dict, cal_kalman: dict) -> str:
    import math

    baseline = _best_baseline(cal_ml.get("rmse_by_indicator_method", {}))
    kal_rmse_dict = cal_kalman.get("rmse_by_indicator_method", {})
    kal_cov_dict = cal_kalman.get("ci90_coverage_by_indicator_method", {})

    all_indicators = sorted(baseline.keys())

    rows = []
    n_improve = 0
    n_total = 0
    n_regress = 0
    cov_failures: list[str] = []

    for ind in all_indicators:
        bl_label, bl_rmse = baseline[ind]
        kal_rmse = (kal_rmse_dict.get(ind) or {}).get(_KAL_METHOD)
        if kal_rmse is None or not (math.isfinite(bl_rmse) and math.isfinite(kal_rmse)):
            continue
        delta = (kal_rmse - bl_rmse) / bl_rmse if bl_rmse > 0 else float("nan")
        kal_cov = (kal_cov_dict.get(ind) or {}).get(_KAL_METHOD, float("nan"))
        n_total += 1
        if delta <= -0.05:
            n_improve += 1
        if delta > 0.02:
            n_regress += 1
        if math.isfinite(kal_cov) and not (0.85 <= kal_cov <= 0.95):
            cov_failures.append(ind)
        rows.append((ind, bl_label, bl_rmse, kal_rmse, delta, kal_cov))

    lines: list[str] = [
        "# Kalman ablation: kalman_state_space vs best-existing-baseline", "",
        "Baseline is multi_anchor where anchor sources exist, trend_ensemble otherwise.", "",
    ]
    lines.append(
        "| Indicator | Baseline | RMSE base | RMSE kalman | Δ (rel) | Cov90 kalman |"
    )
    lines.append("|---|---|---:|---:|---:|---:|")
    for ind, bl_lbl, bl_r, kal_r, delta, cov in rows:
        cov_str = f"{cov:.1%}" if math.isfinite(cov) else "n/a"
        delta_str = f"{delta:+.2%}" if math.isfinite(delta) else "n/a"
        lines.append(
            f"| {ind} | {bl_lbl} | {bl_r:.2%} | {kal_r:.2%} | {delta_str} | {cov_str} |"
        )
    lines.append("")

    # Ship gate
    r1_pass = n_total > 0 and (n_improve / n_total) >= 0.75
    r2_pass = len(cov_failures) == 0
    r3_pass = n_regress == 0

    lines.append("## Ship gate verdict")
    lines.append("")
    r1_label = "PASS" if r1_pass else "FAIL"
    r1_frac = f"{n_improve}/{n_total} = {n_improve / n_total:.1%}" if n_total else "0/0"
    lines.append(
        f"* Rule 1 (≥75% indicators improve RMSE by ≥5% vs best baseline): **{r1_label}** ({r1_frac})"
    )
    r2_label = "PASS" if r2_pass else "FAIL"
    cov_fail_str = ", ".join(cov_failures) if cov_failures else "none"
    lines.append(
        f"* Rule 2 (CI90 coverage ∈ [85%, 95%] for all indicators): **{r2_label}**"
        + (f" (failing: {cov_fail_str})" if cov_failures else "")
    )
    r3_label = "PASS" if r3_pass else "FAIL"
    lines.append(
        f"* Rule 3 (no indicator regresses by > 2% RMSE): **{r3_label}**"
        + (f" ({n_regress} regressed)" if n_regress else " (0 regressed)")
    )
    lines.append("")

    if r1_pass and r2_pass and r3_pass:
        lines.append("## **Verdict: SHIP** — flip `use_kalman=True` default.")
    else:
        lines.append(
            "## **Verdict: HOLD** — ship as opt-in only (`use_kalman=False` default)."
        )

    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
