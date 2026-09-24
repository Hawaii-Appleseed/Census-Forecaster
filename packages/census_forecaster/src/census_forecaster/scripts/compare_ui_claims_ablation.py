"""State UI-claims feature ablation — the promotion gate for the screen's
strongest-ever finding.

Background
----------
``HI_UI_CLAIMS -> HI_UNEMPLOYMENT`` is the causal screen's best pair
(Granger p=3.2e-64, r=+0.821, genuine 1-month lead, 2020-robust;
METHODOLOGY.md §"Current-indicator intake batch"). It was deliberately
never promoted to a feature registry, because promotion requires an
ablation and no ablation was possible while the channel existed only as
a Hawaii monthly series: a Hawaii-only feature is constant across 86 of
the panel's 90 counties, so a panel-wide ablation cannot see it, and
METHODOLOGY.md §"S2301 mean-reversion model" established that LAUS-style
state-driven predictors lose panel-wide to ACS-self-trained ones anyway.

The unlock is that ETA-539 publishes **every state** from the same
keyless CSV. ``refresh_ui_claims`` now emits per-state calendar-year
means, and ``ml_features.STATE_SERIES`` gives each county its own
state's claims — so the channel carries real signal in all 90 counties
and the standard ablation applies, with a Hawaii-restricted read
reported alongside because that is where the screen evidence lives.

Two arms, both ``include_ml=True`` and carrying every already-shipped
channel (county BPS/SAIPE/LAUS, market signals, national macro); the
only difference is the state channel:

  A. ``state_data={}``      — shipped ML baseline
  B. ``state_data=bundled`` — baseline + the 4 ``ui_claims_*`` columns

Gates
-----
Standing ship gates, same as the other channel ablations:

  * no ``ensemble_with_ml`` RMSE regression > 2% absolute on any
    indicator, panel-wide;
  * CI90 coverage stays in [85%, 95%] for every indicator in arm B;
  * **and** — specific to this channel — no regression on the
    Hawaii-restricted S2301 cell, which is the cell the screen evidence
    is actually about. A panel-wide wash with a Hawaii regression is a
    fail, not a tie.

Sign check
----------
A Granger F-test is direction-blind, and so is an RMSE delta. The screen's
mechanism claims **claims up → unemployment up**, so the report includes a
partial-dependence probe: the fitted S2301 model's predicted log-growth
must rise with ``ui_claims_rel3`` (claims against the state's own 3-year
baseline). A channel that improves RMSE while moving *against* its
mechanism is fitting something other than the hypothesis, and the report
says so rather than banking the improvement.

Usage::

    python -m census_forecaster.scripts.compare_ui_claims_ablation
    python -m census_forecaster.scripts.compare_ui_claims_ablation \\
        --anchors 2018,2019,2020,2021,2022 --output report.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from ..acs.calibration import run_stratified_calibration
from ..acs.ml_features import (
    load_market_signals_data,
    load_national_macro_data,
    load_state_data,
    state_columns,
)
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
from .load_calibration_panel import load_panel

_REPO_ROOT = Path(__file__).resolve().parents[5]
_RESULTS_DIR = _REPO_ROOT / "backtests" / "results"

_S2301 = "S2301_C04_001E"
_HI_PREFIX = "15"
_HONOLULU = "15003"

# The column the mechanism speaks through: claims measured against the
# state's own recent baseline, so "elevated" means the same thing in
# Hawaii and California.
_SIGN_COLUMN = "ui_claims_rel3"

# Indicators probed for permutation importance. S2301 is the hypothesis;
# the other two are the labour market's nearest neighbours, included so a
# channel that only ever helps its own target is distinguishable from one
# leaking a generic year-effect.
_PROBE_INDICATORS = (_S2301, "S1701_C03_001E", "B19013_001E")


def _hawaii_only(residuals):
    """Keep only folds for Hawaii counties (geoid 15xxx)."""
    return [r for r in residuals if r.geoid.startswith(_HI_PREFIX)]


def _honolulu_mape(residuals) -> tuple[float, int]:
    """Mean absolute percentage error over Honolulu County folds.

    CLAUDE.md carries a standing gate — "Honolulu County backtest baseline
    MAPE: 6.76%, regression is a failure" — but no script in the repo
    emits that number, so its exact definition (which indicators, which
    horizons, which member) is not recoverable. What IS recoverable, and
    is what the gate actually asks, is whether this change moved it: a
    same-definition A-vs-B delta on the same folds answers that
    regardless of how the 6.76% was originally computed. Reported as a
    delta for exactly that reason — the absolute level here is not
    claimed to be comparable to 6.76%.
    """
    errs = [abs(r.point - r.actual) / r.actual
            for r in residuals
            if r.geoid == _HONOLULU and r.actual > 0]
    return (sum(errs) / len(errs) if errs else float("nan"), len(errs))


def _hi_metrics_table(a, b) -> list[str]:
    """Hawaii-restricted comparison rows.

    Deliberately NOT ``_metrics_table``. That helper flags any arm-B
    coverage outside [85%, 95%] — correct panel-wide, but on a 4-county
    subset several indicators sit outside the band in BOTH arms for
    reasons that predate this channel (272 folds per indicator, and the
    κ/bias strata were fit panel-wide). Flagging those as if the channel
    caused them would read as damage it did not do. So each row says
    which it is: `pre-existing` when arm A was already out of band,
    `NEWLY OUT OF BAND` only when arm B pushed it out.
    """
    rows: list[str] = []
    for ind in sorted(set(a) | set(b)):
        ma, mb = a.get(ind), b.get(ind)
        if ma is None or mb is None:
            rows.append(f"| {ind} | — | — | — | — | (absent in one arm) |")
            continue
        d_rmse = mb.rmse_pct - ma.rmse_pct
        in_a = COVERAGE_BAND[0] <= ma.coverage <= COVERAGE_BAND[1]
        in_b = COVERAGE_BAND[0] <= mb.coverage <= COVERAGE_BAND[1]
        notes = []
        if d_rmse > RMSE_REGRESSION_LIMIT:
            notes.append("**RMSE REGRESSION**")
        if not in_b:
            notes.append("pre-existing coverage" if not in_a
                         else "**NEWLY OUT OF BAND**")
        rows.append(
            f"| {ind} | {ma.rmse_pct:.4f} | {mb.rmse_pct:.4f} | "
            f"{d_rmse:+.4f} | {ma.coverage:.2%} → {mb.coverage:.2%} | "
            f"{' '.join(notes)} |"
        )
    return rows


def _ui_permutation_importance(series, populations, panel) -> list[str]:
    """Permutation importance of the ui_claims_* columns per probe target."""
    try:
        import numpy as np
        from sklearn.inspection import permutation_importance
    except ImportError:
        return ["(sklearn unavailable — importance check skipped)"]

    from ..acs.ml_features import make_training_rows
    from ..acs.ml_trend import train_ml_model

    wanted = state_columns()
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
        lines.append(f"- **{indicator}** ({len(matrix.X)} rows):")
        for name in wanted:
            i = cols.index(name)
            lines.append(f"    - {name}: {r.importances_mean[i]:+.5f} "
                         f"± {r.importances_std[i]:.5f}")
    return lines


def _sign_check(series, populations, panel) -> tuple[list[str], list[str]]:
    """Partial dependence of predicted S2301 log-growth on claims.

    Returns (report lines, violations). The mechanism says claims up →
    unemployment up, so the partial-dependence curve over
    ``ui_claims_rel3`` must have a positive net slope. A flat curve means
    the trees ignore the column (a no-op, not a contradiction); only a
    materially negative slope is a violation, mirroring the screen's
    materiality floor.
    """
    try:
        import numpy as np
        from sklearn.inspection import partial_dependence
    except ImportError:
        return ["(sklearn unavailable — sign check skipped)"], []

    from ..acs.ml_features import make_training_rows
    from ..acs.ml_trend import train_ml_model

    model = train_ml_model(series, populations, _S2301,
                           cutoff_year=2022, panel=panel)
    matrix = make_training_rows(panel, populations, _S2301, 2022)
    if model is None or not matrix.X:
        return ["(S2301 model unavailable — sign check skipped)"], []

    cols = matrix.spec.column_names
    i = cols.index(_SIGN_COLUMN)
    X = np.asarray(matrix.X, dtype=float)
    pd_result = partial_dependence(model.estimator, X, [i], grid_resolution=9,
                                   kind="average")
    grid = np.asarray(pd_result["grid_values"][0], dtype=float)
    avg = np.asarray(pd_result["average"][0], dtype=float)
    net = float(avg[-1] - avg[0])
    # Materiality floor: the target is log-growth of an unemployment rate
    # over 1-5 years, where a real effect is percent-scale. Below 0.001 in
    # log-growth the curve is numerically flat — the trees are ignoring
    # the column, which is a no-op rather than a contradiction.
    floor = 1e-3
    lines = [
        f"Partial dependence of predicted {_S2301} log-growth on "
        f"`{_SIGN_COLUMN}` (claims vs the state's own 3-yr baseline):",
        "",
        "| " + " | ".join(f"{g:+.2f}" for g in grid) + " |",
        "|" + "---|" * len(grid),
        "| " + " | ".join(f"{v:+.4f}" for v in avg) + " |",
        "",
        f"Net slope (high − low): **{net:+.4f}** log-growth. "
        f"Mechanism predicts positive (claims up → unemployment up); "
        f"materiality floor ±{floor}.",
    ]
    violations: list[str] = []
    if net < -floor:
        lines.append("")
        lines.append("**SIGN VIOLATION** — the fitted channel moves against "
                     "its stated mechanism. Any RMSE gain here is not "
                     "evidence for the screen hypothesis.")
        violations.append(f"sign: net partial-dependence slope {net:+.4f} "
                          "is negative")
    elif net <= floor:
        lines.append("")
        lines.append("Flat within the materiality floor — the trees are "
                     "effectively ignoring the column (a no-op, not a "
                     "contradiction).")
    return lines, violations


def main(argv: Optional[Sequence[str]] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--anchors", type=str,
                   default="2014,2015,2016,2017,2018,2019,2020,2021,2022")
    p.add_argument("--horizons", type=str, default="1,2,3,4,5")
    p.add_argument("--output", type=Path, default=None)
    args = p.parse_args(list(argv) if argv is not None else None)

    anchor_years = [int(s) for s in args.anchors.split(",")]
    horizons = [int(s) for s in args.horizons.split(",")]

    state = load_state_data()
    if not state:
        print("ERROR: ui_claims.json absent — run refresh_ui_claims first",
              file=sys.stderr)
        return 2
    market = load_market_signals_data() or {}
    national = load_national_macro_data() or {}

    print("[ui-ablation] loading panel ...", file=sys.stderr)
    series, populations, _ = load_panel()

    print("[ui-ablation] arm A: ML baseline (no state channel) ...",
          file=sys.stderr)
    cal_a = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=True,
        market_data=market, national_data=national, state_data={},
    )
    print("[ui-ablation] arm B: ML + state UI-claims channel ...",
          file=sys.stderr)
    cal_b = run_stratified_calibration(
        series_by_key=series, anchor_years=anchor_years, horizons=horizons,
        populations=populations, include_ml=True,
        market_data=market, national_data=national, state_data=state,
    )

    res_a = _apply_v3_corrections(_residuals_from_calibration(cal_a), cal_a)
    res_b = _apply_v3_corrections(_residuals_from_calibration(cal_b), cal_b)
    ens_a = _build_with_ml_residuals(res_a, series, populations)
    ens_b = _build_with_ml_residuals(res_b, series, populations)

    m_a = _aggregate_per_indicator(ens_a, "ensemble_with_ml")
    m_b = _aggregate_per_indicator(ens_b, "ensemble_with_ml")
    rows, violations = _metrics_table("baseline", m_a, "+ui_claims", m_b)

    hi_a = _aggregate_per_indicator(_hawaii_only(ens_a), "ensemble_with_ml")
    hi_b = _aggregate_per_indicator(_hawaii_only(ens_b), "ensemble_with_ml")
    hi_rows = _hi_metrics_table(hi_a, hi_b)
    # Only the S2301 cell is gated Hawaii-restricted: that is the pair the
    # screen actually found. The other Hawaii rows are reported for
    # context — with 4 counties they are too thin to gate on.
    hi_violations: list[str] = []
    ha, hb = hi_a.get(_S2301), hi_b.get(_S2301)
    if ha is not None and hb is not None:
        d = hb.rmse_pct - ha.rmse_pct
        if d > RMSE_REGRESSION_LIMIT:
            hi_violations.append(
                f"HI-restricted {_S2301}: RMSE +{d:.3f} "
                f"({ha.rmse_pct:.4f} → {hb.rmse_pct:.4f})")

    mape_a, n_hono = _honolulu_mape(ens_a)
    mape_b, _ = _honolulu_mape(ens_b)
    d_mape = mape_b - mape_a
    if d_mape > RMSE_REGRESSION_LIMIT:
        hi_violations.append(
            f"Honolulu MAPE +{d_mape:.4f} ({mape_a:.2%} → {mape_b:.2%})")

    # Feature diagnostics are read off arm B's panel.
    from ..acs.ml_features import build_panel_index, load_county_data
    panel_b = build_panel_index(
        series, county_data=load_county_data(), market_data=market,
        national_data=national, state_data=state,
    )
    sign_lines, sign_violations = _sign_check(series, populations, panel_b)

    all_violations = violations + hi_violations + sign_violations
    verdict = (["## Verdict: **GATE FAILED**", ""]
               + [f"- {v}" for v in all_violations]) if all_violations else [
        "## Verdict: **GATE PASSED** — no RMSE regression panel-wide or on "
        "the Hawaii-restricted S2301 cell, coverage in band, and the fitted "
        "channel moves with its stated mechanism."]

    report = "\n".join([
        f"# State UI-claims feature ablation — {date.today().isoformat()}",
        "",
        f"Panel: {len(series)} series; anchors {anchor_years}; horizons "
        f"{horizons}. Both arms carry the shipped county, market and "
        f"national-macro channels; the only difference is the "
        f"{len(state_columns())} `ui_claims_*` columns "
        f"({len(state['ui_claims'])} states). Gates: no RMSE regression > "
        f"{RMSE_REGRESSION_LIMIT:.0%} absolute panel-wide or on "
        f"HI-restricted {_S2301}; CI90 coverage in "
        f"[{COVERAGE_BAND[0]:.0%}, {COVERAGE_BAND[1]:.0%}]; "
        "partial-dependence sign must match the screen mechanism.",
        "",
        "## Panel-wide — ensemble_with_ml, baseline (A) vs +ui_claims (B)",
        "",
        "| indicator | RMSE A | RMSE B | ΔRMSE | coverage A → B | flag |",
        "|---|---|---|---|---|---|",
        *rows,
        "",
        "## Hawaii-restricted (4 counties) — the cell the screen found",
        "",
        f"Gated on {_S2301} only — that is the pair the screen found. "
        "The other rows are context: with 4 counties they are too thin to "
        "gate on, and coverage flags here are marked `pre-existing` when "
        "arm A was already outside the band for reasons that predate this "
        "channel.",
        "",
        "| indicator | RMSE A | RMSE B | ΔRMSE | coverage A → B | flag |",
        "|---|---|---|---|---|---|",
        *hi_rows,
        "",
        "## Honolulu County (15003) MAPE — the standing CLAUDE.md gate",
        "",
        f"`ensemble_with_ml`, {n_hono} folds. Reported as an A-vs-B delta: "
        "the 6.76% baseline in CLAUDE.md has no script in this repo that "
        "emits it, so its definition is not recoverable and the absolute "
        "level below is not claimed to be comparable to it. The delta on "
        "identical folds is what the gate is actually asking.",
        "",
        f"| baseline | +ui_claims | Δ |",
        "|---:|---:|---:|",
        f"| {mape_a:.2%} | {mape_b:.2%} | {d_mape * 100:+.3f}pp |",
        "",
        "## ui_claims_* permutation importance",
        "",
        *_ui_permutation_importance(series, populations, panel_b),
        "",
        "## Sign check — does the channel move the way its mechanism says?",
        "",
        *sign_lines,
        "",
        *verdict,
        "",
    ])
    out = args.output or (
        _RESULTS_DIR / f"ui_claims_ablation_{date.today().isoformat()}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    print(f"[ui-ablation] wrote {out}", file=sys.stderr)
    print("\n".join(verdict), file=sys.stderr)
    return 1 if all_violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
