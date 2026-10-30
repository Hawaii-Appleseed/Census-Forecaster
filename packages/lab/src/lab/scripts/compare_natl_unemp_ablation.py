"""National-unemployment feature ablation (registry form).

Originally the ship gate for the bespoke ``natl_unemp_data`` channel
(verdict: GATE PASSED — natl_unemp_ablation_2026-07-15.md). National
unemployment has since been migrated into the NATIONAL_SERIES registry
(``unemp``, col_policy ``level_diff2`` — numerically identical values,
``lag0`` renamed ``lvl``), so this script now expresses the same two arms
through the generic channel:

  A. national_data WITHOUT the "unemp" key — everything else present
  B. national_data with "unemp"           — full registry

Gate unchanged: no ensemble_with_ml RMSE regression > 2% absolute;
CI90 coverage in [85%, 95%]. Permutation importance for the three
natl_unemp_* columns.

Usage::

    python -m lab.scripts.compare_natl_unemp_ablation
"""
from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from census_forecaster.acs.calibration import run_stratified_calibration
from census_forecaster.acs.ml_features import (
    load_market_signals_data,
    load_national_macro_data,
)
from census_forecaster.scripts.load_calibration_panel import load_panel

from .compare_market_ablation import (
    COVERAGE_BAND,
    RMSE_REGRESSION_LIMIT,
    _metrics_table,
)
from .compare_ml_ablation import (
    _aggregate_per_indicator,
    _apply_v3_corrections,
    _build_with_ml_residuals,
    _residuals_from_calibration,
)

_REPO_ROOT = Path(__file__).resolve().parents[5]
_RESULTS_DIR = _REPO_ROOT / "backtests" / "results"

_NATL_COLS = ("natl_unemp_lvl", "natl_unemp_chg1", "natl_unemp_chg2")


def _natl_permutation_importance(series, populations, market,
                                 national) -> list[str]:
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
        market_data=market,
        national_data=national,
    )
    lines = []
    for indicator in ("B19013_001E", "S1701_C03_001E", "S2301_C04_001E"):
        model = train_ml_model(series, populations, indicator,
                               cutoff_year=2022, panel=panel)
        if model is None:
            lines.append(f"- {indicator}: model unavailable")
            continue
        matrix = make_training_rows(panel, populations, indicator, 2022)
        if not matrix.X:
            lines.append(f"- {indicator}: no rows")
            continue
        X = np.asarray(matrix.X, dtype=float)
        y = np.asarray(matrix.y, dtype=float)
        r = permutation_importance(model.estimator, X, y, n_repeats=5,
                                   random_state=0)
        cols = matrix.spec.column_names
        for name in _NATL_COLS:
            i = cols.index(name)
            lines.append(
                f"- {indicator} / {name}: "
                f"{r.importances_mean[i]:+.5f} ± {r.importances_std[i]:.5f}")
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--anchors", type=str,
                   default="2014,2015,2016,2017,2018,2019,2020,2021,2022")
    p.add_argument("--horizons", type=str, default="1,2,3,4,5")
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args(list(argv) if argv is not None else None)

    anchor_years = [int(s) for s in args.anchors.split(",")]
    horizons = [int(s) for s in args.horizons.split(",")]

    national = load_national_macro_data()
    if national is None or "unemp" not in national:
        print("ERROR: national_macro.json absent or missing 'unemp' — run "
              "refresh_national_macro first", file=sys.stderr)
        return 2
    market = load_market_signals_data() or {}
    national_sans = {k: v for k, v in national.items() if k != "unemp"}

    print("[natl-ablation] loading panel ...", file=sys.stderr)
    series, populations, _ = load_panel()

    print("[natl-ablation] arm A: registry WITHOUT unemp ...", file=sys.stderr)
    cal_a = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=True,
        market_data=market, national_data=national_sans,
    )
    print("[natl-ablation] arm B: full registry (with unemp) ...",
          file=sys.stderr)
    cal_b = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=True,
        market_data=market, national_data=national,
    )

    res_a = _apply_v3_corrections(_residuals_from_calibration(cal_a), cal_a)
    res_b = _apply_v3_corrections(_residuals_from_calibration(cal_b), cal_b)
    ens_a = _build_with_ml_residuals(res_a, series, populations)
    ens_b = _build_with_ml_residuals(res_b, series, populations)
    m_a = _aggregate_per_indicator(list(res_a) + ens_a, "ensemble_with_ml")
    m_b = _aggregate_per_indicator(list(res_b) + ens_b, "ensemble_with_ml")
    rows, violations = _metrics_table("sans-unemp", m_a, "+unemp", m_b)

    verdict = (["## Verdict: **GATE FAILED**", ""]
               + [f"- {v}" for v in violations]) if violations else \
        ["## Verdict: **GATE PASSED** — no RMSE regression, coverage in "
         "band. (`use_ml` remains opt-in.)"]

    report = "\n".join([
        f"# National-unemployment feature ablation (registry form) — "
        f"{date.today().isoformat()}",
        "",
        f"Panel: {len(series)} series; anchors {anchor_years}; "
        f"horizons {horizons}. Gates: no RMSE regression > "
        f"{RMSE_REGRESSION_LIMIT:.0%} absolute; CI90 coverage in "
        f"[{COVERAGE_BAND[0]:.0%}, {COVERAGE_BAND[1]:.0%}]. Both arms carry "
        "mkt_* + the rest of the national-macro registry; the only "
        "difference is the 'unemp' registry entry (natl_unemp_lvl/chg1/"
        "chg2 — the migrated form of the former bespoke channel).",
        "",
        "## ensemble_with_ml — sans-unemp (A) vs +unemp (B)",
        "",
        "| indicator | RMSE A | RMSE B | ΔRMSE | coverage A → B | flag |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        "## natl_unemp_* permutation importance",
        "",
        *_natl_permutation_importance(series, populations, market, national),
        "",
        *verdict,
        "",
    ])
    out = args.output or (
        _RESULTS_DIR / f"natl_unemp_ablation_{date.today().isoformat()}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"[natl-ablation] wrote {out}", file=sys.stderr)
    print("\n".join(verdict), file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
