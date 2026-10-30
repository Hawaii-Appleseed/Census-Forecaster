"""Market-signals ablation: do the mkt_* features and the national
unemployment anchor earn their keep? (Phase-3 ship gate.)

Four calibration arms on the bundled panel:

  A. include_ml=True,  market_data={}       — ML baseline (no mkt features)
  B. include_ml=True,  market_data=bundled  — ML with mkt features
  C. include_ml=False, national-unemployment registry row REMOVED
  D. include_ml=False, registry intact                       (anchor arm)

Ship gates (mirrors compare_ml_ablation.py):
  * mkt features: ensemble_with_ml RMSE must not regress by > 2%
    absolute on any indicator; CI90 coverage stays in [85%, 95%].
    (Improvement is a bonus, not required — ml stays opt-in either way;
    a NaN-safe no-op is an acceptable outcome for geoid-constant
    year-effect features.)
  * anchor row: S2301 RMSE/coverage must not regress D-vs-C.

Also reports HGB permutation importance of the mkt_* columns so
"the trees simply ignore them" is measurable (they are year-effects,
near-collinear with anchor_year_norm — see METHODOLOGY.md).

Usage::

    python -m lab.scripts.compare_market_ablation
    python -m lab.scripts.compare_market_ablation \\
        --anchors 2018,2019,2020,2021,2022 --output report.md
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from census_forecaster.acs.calibration import run_stratified_calibration
from census_forecaster.acs.ml_features import load_market_signals_data
from census_forecaster.acs.sources import base as sources_base
from census_forecaster.scripts.load_calibration_panel import load_panel

from .compare_ml_ablation import (
    IndicatorMetrics,
    _aggregate_per_indicator,
    _apply_v3_corrections,
    _build_with_ml_residuals,
    _residuals_from_calibration,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_RESULTS_DIR = _REPO_ROOT / "backtests" / "results"

RMSE_REGRESSION_LIMIT = 0.02   # no indicator may regress by > 2% absolute
COVERAGE_BAND = (0.85, 0.95)

_ANCHOR_ROW_FILE = "bls_national_unemployment.json"
_S2301 = "S2301_C04_001E"


def _metrics_table(
    label_a: str, a: dict[str, IndicatorMetrics],
    label_b: str, b: dict[str, IndicatorMetrics],
) -> tuple[list[str], list[str]]:
    """Markdown rows comparing two per-indicator metric dicts.

    Returns (rows, violations). A violation is an RMSE regression beyond
    RMSE_REGRESSION_LIMIT or coverage leaving COVERAGE_BAND in arm B.
    """
    rows, violations = [], []
    for ind in sorted(set(a) | set(b)):
        ma, mb = a.get(ind), b.get(ind)
        if ma is None and mb is not None:
            # Member newly exists in arm B (e.g. first rate anchor for an
            # indicator): show its standalone metrics; the blended-ensemble
            # table below is where regression would surface.
            rows.append(
                f"| {ind} | (absent) | {mb.rmse_pct:.4f} | new member | "
                f"— → {mb.coverage:.2%} | new in {label_b} |")
            continue
        if mb is None:
            rows.append(
                f"| {ind} | {ma.rmse_pct:.4f} | (absent) | removed | "
                f"{ma.coverage:.2%} → — | only in {label_a} |")
            continue
        d_rmse = mb.rmse_pct - ma.rmse_pct
        flag = ""
        if d_rmse > RMSE_REGRESSION_LIMIT:
            flag = "**RMSE REGRESSION**"
            violations.append(f"{ind}: RMSE +{d_rmse:.3f}")
        if not (COVERAGE_BAND[0] <= mb.coverage <= COVERAGE_BAND[1]):
            flag += " **COVERAGE**"
            violations.append(f"{ind}: coverage {mb.coverage:.2%}")
        rows.append(
            f"| {ind} | {ma.rmse_pct:.4f} | {mb.rmse_pct:.4f} | "
            f"{d_rmse:+.4f} | {ma.coverage:.2%} → {mb.coverage:.2%} | "
            f"{flag} |"
        )
    return rows, violations


def _mkt_permutation_importance(series, populations) -> list[str]:
    """Permutation importance of the mkt_* columns on one HGB fit."""
    try:
        import numpy as np
        from sklearn.inspection import permutation_importance
    except ImportError:
        return ["(sklearn unavailable — importance check skipped)"]

    from census_forecaster.acs.ml_features import (
        build_panel_index,
        load_county_data,
        make_training_rows,
    )
    from census_forecaster.acs.ml_trend import train_ml_model

    panel = build_panel_index(
        series,
        county_data=load_county_data(),
        market_data=load_market_signals_data(),
    )
    lines = []
    for indicator in ("B19013_001E", "B25077_001E", _S2301):
        model = train_ml_model(series, populations, indicator,
                               cutoff_year=2022, panel=panel)
        if model is None:
            lines.append(f"- {indicator}: model unavailable")
            continue
        matrix = make_training_rows(panel, populations, indicator, 2022)
        if not matrix.X:
            lines.append(f"- {indicator}: no rows for importance")
            continue
        X = np.asarray(matrix.X, dtype=float)
        y = np.asarray(matrix.y, dtype=float)
        r = permutation_importance(model.estimator, X, y, n_repeats=5,
                                   random_state=0)
        cols = matrix.spec.column_names
        for name in ("mkt_energy_mom_lag0", "mkt_shipping_mom_lag0",
                     "mkt_reit_mom_lag0", "mkt_reit_mom_lag1"):
            i = cols.index(name)
            lines.append(
                f"- {indicator} / {name}: "
                f"{r.importances_mean[i]:+.5f} ± {r.importances_std[i]:.5f}"
            )
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--anchors", type=str,
                   default="2014,2015,2016,2017,2018,2019,2020,2021,2022")
    p.add_argument("--horizons", type=str, default="1,2,3,4,5")
    p.add_argument("--output", type=Path, default=None)
    p.add_argument("--skip-ml", action="store_true",
                   help="Skip the (slow) ML arms; anchor arms only.")
    p.add_argument("--skip-anchor", action="store_true",
                   help="Skip the anchor arms; ML arms only.")
    args = p.parse_args(list(argv) if argv is not None else None)

    anchor_years = [int(s) for s in args.anchors.split(",")]
    horizons = [int(s) for s in args.horizons.split(",")]

    market = load_market_signals_data()
    if market is None:
        print("ERROR: market_signals.json absent — run refresh_market_panel "
              "--derive-signals first", file=sys.stderr)
        return 2

    print("[mkt-ablation] loading panel ...", file=sys.stderr)
    series, populations, _manifest = load_panel()

    sections: list[str] = [
        f"# Market-signals ablation — {date.today().isoformat()}",
        "",
        f"Panel: {len(series)} series; anchors {anchor_years}; "
        f"horizons {horizons}. Gates: no RMSE regression > "
        f"{RMSE_REGRESSION_LIMIT:.0%} absolute; CI90 coverage in "
        f"[{COVERAGE_BAND[0]:.0%}, {COVERAGE_BAND[1]:.0%}].",
        "",
    ]
    all_violations: list[str] = []

    # ---- Arms A/B: ML features ----
    if not args.skip_ml:
        print("[mkt-ablation] arm A: ML without mkt features ...",
              file=sys.stderr)
        cal_a = run_stratified_calibration(
            series_by_key=series, anchor_years=anchor_years,
            horizons=horizons, populations=populations,
            include_ml=True, market_data={},
        )
        print("[mkt-ablation] arm B: ML with mkt features ...",
              file=sys.stderr)
        cal_b = run_stratified_calibration(
            series_by_key=series, anchor_years=anchor_years,
            horizons=horizons, populations=populations,
            include_ml=True, market_data=market,
        )
        res_a = _apply_v3_corrections(_residuals_from_calibration(cal_a), cal_a)
        res_b = _apply_v3_corrections(_residuals_from_calibration(cal_b), cal_b)
        ens_a = _build_with_ml_residuals(res_a, series, populations)
        ens_b = _build_with_ml_residuals(res_b, series, populations)
        m_a = _aggregate_per_indicator(list(res_a) + ens_a, "ensemble_with_ml")
        m_b = _aggregate_per_indicator(list(res_b) + ens_b, "ensemble_with_ml")
        rows, violations = _metrics_table("no-mkt", m_a, "with-mkt", m_b)
        all_violations += [f"[ml] {v}" for v in violations]
        sections += [
            "## ML arms — ensemble_with_ml, no-mkt (A) vs with-mkt (B)",
            "",
            "| indicator | RMSE A | RMSE B | ΔRMSE | coverage A → B | flag |",
            "|---|---|---|---|---|---|",
            *rows,
            "",
            "### mkt_* permutation importance (year-effect collinearity check)",
            "",
            *_mkt_permutation_importance(series, populations),
            "",
        ]

    # ---- Arms C/D: national unemployment anchor ----
    if not args.skip_anchor:
        original_spec = sources_base._REGISTRY_SPEC
        filtered = [s for s in original_spec if s[0] != _ANCHOR_ROW_FILE]
        try:
            print("[mkt-ablation] arm C: anchor row removed ...",
                  file=sys.stderr)
            sources_base._REGISTRY_SPEC = filtered
            cal_c = run_stratified_calibration(
                series_by_key=series, anchor_years=anchor_years,
                horizons=horizons, populations=populations, include_ml=False,
            )
        finally:
            sources_base._REGISTRY_SPEC = original_spec
        print("[mkt-ablation] arm D: anchor row active ...", file=sys.stderr)
        cal_d = run_stratified_calibration(
            series_by_key=series, anchor_years=anchor_years,
            horizons=horizons, populations=populations, include_ml=False,
        )
        res_c = _apply_v3_corrections(_residuals_from_calibration(cal_c), cal_c)
        res_d = _apply_v3_corrections(_residuals_from_calibration(cal_d), cal_d)
        # Blended trend+anchor ensemble (what forecasts actually ship):
        # _build_with_ml_residuals degrades to trend⊕anchor when no ml
        # residuals exist, which is exactly the include_ml=False blend.
        ens_c = _build_with_ml_residuals(res_c, series, populations)
        ens_d = _build_with_ml_residuals(res_d, series, populations)
        m_c = {k: v for k, v in _aggregate_per_indicator(
            ens_c, "ensemble_with_ml").items() if k == _S2301}
        m_d = {k: v for k, v in _aggregate_per_indicator(
            ens_d, "ensemble_with_ml").items() if k == _S2301}
        rows, violations = _metrics_table("no-row", m_c, "with-row", m_d)
        all_violations += [f"[anchor/blended] {v}" for v in violations]
        sections += [
            f"## Anchor arms — blended trend⊕anchor ensemble on {_S2301}, "
            "row removed (C) vs active (D)",
            "",
            "| indicator | RMSE C | RMSE D | ΔRMSE | coverage C → D | flag |",
            "|---|---|---|---|---|---|",
            *rows,
            "",
        ]
        for method in ("level_anchor", "multi_anchor", "trend_ensemble"):
            m_c = {k: v for k, v in
                   _aggregate_per_indicator(res_c, method).items()
                   if k == _S2301}
            m_d = {k: v for k, v in
                   _aggregate_per_indicator(res_d, method).items()
                   if k == _S2301}
            if not (m_c or m_d):
                continue
            rows, violations = _metrics_table("no-row", m_c, "with-row", m_d)
            all_violations += [f"[anchor/{method}] {v}" for v in violations]
            sections += [
                f"## Anchor arms — {method} on {_S2301}, "
                "row removed (C) vs active (D)",
                "",
                "| indicator | RMSE C | RMSE D | ΔRMSE | coverage C → D | flag |",
                "|---|---|---|---|---|---|",
                *rows,
                "",
            ]

    # ---- verdict ----
    if all_violations:
        verdict = ["## Verdict: **GATE FAILED**", ""] + \
                  [f"- {v}" for v in all_violations]
    else:
        verdict = ["## Verdict: **GATE PASSED** — no RMSE regression, "
                   "coverage in band. (`use_ml` remains opt-in regardless.)"]
    sections += verdict + [""]

    report = "\n".join(sections)
    out = args.output or (
        _RESULTS_DIR / f"market_ml_ablation_{date.today().isoformat()}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"[mkt-ablation] wrote {out}", file=sys.stderr)
    print("\n".join(verdict), file=sys.stderr)
    return 1 if all_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
