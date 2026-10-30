"""National-macro feature ablation (Phase-4 ship gate).

Tests whether the 13-series national-macro registry (19 columns: national
CPI, wages, labour-force participation, JOLTS, mortgage/10yr rates, HVS
vacancy/homeownership) earns its keep as ML features on the bundled panel.

Two arms, both include_ml=True and carrying the already-shipped mkt_*
features; the only difference is the national_data channel (which now
includes national unemployment via the level_diff2 registry entry):

  A. national_data={}    — shipped ML baseline
  B. national_data=real  — baseline + national-macro registry columns

Gate (mirrors the other ablations): no ensemble_with_ml RMSE regression
> 2% absolute on any indicator; CI90 coverage stays in [85%, 95%];
Honolulu MAPE ≤ 6.76% untouched (default path is opt-in regardless).

Permutation importance is reported for all 19 columns (trustworthy now
that column_names matches the real row order), grouped by the four target
families the series map to. Because 19 geoid-constant columns are
near-collinear with anchor_year_norm (year-effects), expect an
ensemble-level wash — the importance table is the decision artifact.

Usage::

    python -m lab.scripts.compare_national_macro_ablation
    python -m lab.scripts.compare_national_macro_ablation \\
        --anchors 2018,2019,2020,2021,2022 --output report.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from census_forecaster.acs.calibration import run_stratified_calibration
from census_forecaster.acs.ml_features import (
    load_market_signals_data,
    load_national_macro_data,
    national_macro_columns,
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

# Representative target per family for the permutation-importance probe.
_PROBE_INDICATORS = (
    "B19013_001E",       # income  (wages, CPI)
    "B25077_001E",       # home value (mortgage, 10yr, HVS, CPI housing)
    "S2301_C04_001E",    # unemployment (LFPR, emp-pop, JOLTS)
    "S1701_C03_001E",    # poverty (broad labour/price)
)


def _national_permutation_importance(series, populations, market,
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
    cols_wanted = national_macro_columns()
    lines: list[str] = []
    for indicator in _PROBE_INDICATORS:
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
        # Report the 3 strongest national columns for this indicator.
        scored = sorted(
            ((name, r.importances_mean[cols.index(name)],
              r.importances_std[cols.index(name)]) for name in cols_wanted),
            key=lambda t: t[1], reverse=True)
        lines.append(f"- **{indicator}** top national features:")
        for name, mean, std in scored[:3]:
            lines.append(f"    - {name}: {mean:+.5f} ± {std:.5f}")
    return lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--anchors", type=str,
                   default="2014,2015,2016,2017,2018,2019,2020,2021,2022")
    p.add_argument("--horizons", type=str, default="1,2,3,4,5")
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args(list(argv) if argv is not None else None)

    anchor_years = [int(s) for s in args.anchors.split(",")]
    horizons = [int(s) for s in args.horizons.split(",")]

    national = load_national_macro_data()
    if national is None:
        print("ERROR: national_macro.json absent — run "
              "refresh_national_macro first", file=sys.stderr)
        return 2
    market = load_market_signals_data() or {}

    print("[nm-ablation] loading panel ...", file=sys.stderr)
    series, populations, _ = load_panel()

    print("[nm-ablation] arm A: ML baseline (no national-macro) ...",
          file=sys.stderr)
    cal_a = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=True,
        market_data=market, national_data={},
    )
    print("[nm-ablation] arm B: ML + national-macro registry ...",
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
    rows, violations = _metrics_table("baseline", m_a, "+national", m_b)

    verdict = (["## Verdict: **GATE FAILED**", ""]
               + [f"- {v}" for v in violations]) if violations else \
        ["## Verdict: **GATE PASSED** — no RMSE regression, coverage in "
         "band. (`use_ml` remains opt-in.)"]

    report = "\n".join([
        f"# National-macro feature ablation — {date.today().isoformat()}",
        "",
        f"Panel: {len(series)} series; anchors {anchor_years}; "
        f"horizons {horizons}. Gates: no RMSE regression > "
        f"{RMSE_REGRESSION_LIMIT:.0%} absolute; CI90 coverage in "
        f"[{COVERAGE_BAND[0]:.0%}, {COVERAGE_BAND[1]:.0%}]. Both arms carry "
        "the shipped mkt_* features; the only difference is "
        f"the {len(national_macro_columns())} national-macro columns "
        f"(14 series incl. national unemployment).",
        "",
        "## ensemble_with_ml — baseline (A) vs +national-macro (B)",
        "",
        "| indicator | RMSE A | RMSE B | ΔRMSE | coverage A → B | flag |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        "## national-macro permutation importance (top 3 per target)",
        "",
        *_national_permutation_importance(
            series, populations, market, national),
        "",
        *verdict,
        "",
    ])
    out = args.output or (
        _RESULTS_DIR / f"national_macro_ablation_{date.today().isoformat()}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"[nm-ablation] wrote {out}", file=sys.stderr)
    print("\n".join(verdict), file=sys.stderr)
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
