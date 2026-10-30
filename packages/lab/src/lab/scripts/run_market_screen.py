"""Run the market-signal causal screen (fully offline, committed data only).

Reads ``data/markets/prices_panel.json``, ``data/markets/macro_monthly.json``,
``data/bls_panel/cpi_panel.json``, and the annual county anchors; writes

* ``backtests/results/market_signal_screen_<date>.md`` — human review doc
  (the Phase-3 gate requires a human to read this before integration), and
* ``data/markets/selected_signals.json`` — machine-readable BH survivors.

Usage
-----
    python -m lab.scripts.run_market_screen
    python -m lab.scripts.run_market_screen --q 0.10 --max-lead 18
    python -m lab.scripts.run_market_screen --dry-run
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

from ..markets.panel import load_prices_panel
from ..markets.screen import (
    HYPOTHESIS_PAIRS,
    HAWAII_PREDICTORS,
    MONTHLY_TARGETS,
    NATIONAL_PREDICTORS,
    ScreenReport,
    annual_descriptive_leads,
    assert_sign_coverage,
    expected_sign,
    month_index,
    run_screen,
    series_to_monthly_dict,
)
import census_forecaster
from census_forecaster.scripts.refresh_zillow_laus_anchors import _atomic_write_json

_PKG_DATA = Path(census_forecaster.__file__).resolve().parent / "data"
_MARKETS_DIR = _PKG_DATA / "markets"
_REPO_ROOT = Path(__file__).resolve().parents[5]
_RESULTS_DIR = _REPO_ROOT / "backtests" / "results"

HONOLULU_GEOID = "15003"

# Annual descriptive pairs: ticker YoY log change vs Hawaii county-anchor
# YoY change. These are the forecaster's actual connection points.
ANNUAL_PAIRS = (
    ("SPY", "LAUS_HI_UNEMPLOYMENT"),
    ("JETS", "LAUS_HI_UNEMPLOYMENT"),
    ("BOH", "SAIPE_HI_POVERTY"),
    ("XLRE", "ZHVI_HONOLULU_ANNUAL"),
    ("VNQ", "ZHVI_HONOLULU_ANNUAL"),
    ("MATX", "ZHVI_HONOLULU_ANNUAL"),
)


def _load_json(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)


def load_monthly_targets() -> dict[str, dict[int, float]]:
    """Resolve every MONTHLY_TARGETS key to {month_index: value}."""
    out: dict[str, dict[int, float]] = {}
    macro_path = _MARKETS_DIR / "macro_monthly.json"
    macro = _load_json(macro_path)["series"] if macro_path.exists() else {}
    cpi_path = _PKG_DATA / "bls_panel" / "cpi_panel.json"
    cpi = _load_json(cpi_path)["series"] if cpi_path.exists() else {}

    for key, (source_id, _tf) in MONTHLY_TARGETS.items():
        rows = macro.get(source_id) or cpi.get(source_id)
        if rows:
            out[key] = series_to_monthly_dict(rows)
    return out


def load_national_monthly_predictors() -> dict[str, dict[int, float]]:
    """National-macro monthly predictors keyed by their US_* screen name.

    Reads the raw monthly levels from macro_monthly.json (written by
    refresh_national_macro) and returns ``{US_NAME: {month_index: level}}``
    for merging into the screen's predictor dict alongside the tickers.
    """
    out: dict[str, dict[int, float]] = {}
    macro_path = _MARKETS_DIR / "macro_monthly.json"
    macro = _load_json(macro_path)["series"] if macro_path.exists() else {}
    for name, source_id in {**NATIONAL_PREDICTORS, **HAWAII_PREDICTORS}.items():
        rows = macro.get(source_id)
        if rows:
            out[name] = series_to_monthly_dict(rows)
    return out


def load_annual_targets() -> dict[str, dict[int, float]]:
    """Annual Hawaii anchors as stationary {year: change} dicts."""
    anchors = _PKG_DATA / "anchors"
    out: dict[str, dict[int, float]] = {}

    def yoy_diff(values_by_year: dict[str, float]) -> dict[int, float]:
        yrs = {int(y): v for y, v in values_by_year.items()}
        return {y: yrs[y] - yrs[y - 1] for y in yrs if (y - 1) in yrs}

    def yoy_logdiff(values_by_year: dict[str, float]) -> dict[int, float]:
        yrs = {int(y): v for y, v in values_by_year.items()}
        return {y: math.log(yrs[y] / yrs[y - 1]) for y in yrs
                if (y - 1) in yrs and yrs[y] > 0 and yrs[y - 1] > 0}

    specs = (
        ("bls_laus.json", "LAUS_HI_UNEMPLOYMENT", yoy_diff),
        ("saipe_poverty.json", "SAIPE_HI_POVERTY", yoy_diff),
        ("zillow_zhvi.json", "ZHVI_HONOLULU_ANNUAL", yoy_logdiff),
    )
    for fname, key, xform in specs:
        path = anchors / fname
        if not path.exists():
            continue
        payload = _load_json(path)
        county = payload.get("values_by_geoid_year", {}).get(HONOLULU_GEOID)
        if county:
            out[key] = xform(county)
    return out


def ticker_month_series(panel) -> dict[str, dict[int, float]]:
    return {
        sym: {month_index(b.year, b.month): b.adj_close
              for b in panel.bars(sym)}
        for sym in panel.symbols()
    }


def ticker_annual_logdiff(panel) -> dict[str, dict[int, float]]:
    """December-to-December log change per ticker (matches the anchors'
    december aggregation convention)."""
    out: dict[str, dict[int, float]] = {}
    for sym in panel.symbols():
        dec = {b.year: b.adj_close for b in panel.bars(sym) if b.month == 12}
        out[sym] = {y: math.log(dec[y] / dec[y - 1]) for y in dec
                    if (y - 1) in dec and dec[y] > 0 and dec[y - 1] > 0}
    return out


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _fmt_sign(c) -> str:
    """Sign verdict vs the pre-registered mechanism direction."""
    if c.sign_ok is None:
        return "n/a"
    return "ok" if c.sign_ok else "**WRONG**"


def _fmt_candidate_row(c) -> str:
    g = c.granger
    x = c.best_xcorr
    return (
        f"| {c.ticker} | {c.transform} | {c.target} | {c.lags or '—'} | "
        f"{f'{g.f_stat:.2f}' if g else '—'} | "
        f"{f'{g.p_value:.4f}' if g else '—'} | "
        f"{g.nobs if g else '—'} | "
        f"{f'{x.r:+.3f}@{x.lead}m' if x else '—'} | "
        f"{_fmt_sign(c)} | "
        f"{'**YES**' if c.bh_pass else 'no'} | {c.note} |"
    )


def _sign_violation_lines(base: ScreenReport,
                          sans2020: ScreenReport) -> list[str]:
    """Rows whose correlation contradicts their own mechanism."""
    seen: set[tuple] = set()
    rows: list[str] = []
    for label, rep in (("full sample", base), ("2020 excluded", sans2020)):
        for c in rep.candidates:
            if c.sign_ok is not False:
                continue
            key = (c.ticker, c.target, c.lags, label)
            if key in seen:
                continue
            seen.add(key)
            want = expected_sign(c.ticker, c.target)
            rows.append(
                f"| {c.ticker} | {c.target} | {label} | {c.lags} | "
                f"{'+' if want and want > 0 else '-'} | "
                f"{f'{c.sign_stat:+.3f}' if c.sign_stat is not None else '—'} | "
                f"{'**yes**' if c.bh_pass else 'no'} |")
    if not rows:
        return ["_No registered pair contradicts its own predicted "
                "direction in either run._"]
    return ([f"**{len(rows)} pair/run combination(s) contradict their "
             "predicted direction.**", "",
             "| predictor | target | run | lag | predicted | "
             "mean r (leads 1..lag) | cleared BH |",
             "|---|---|---|---|---|---|---|"] + rows)


def render_markdown(
    base: ScreenReport,
    sans2020: ScreenReport,
    annual_rows: list[dict],
    *,
    run_date: str,
) -> str:
    lines = [
        f"# Market-signal screen — {run_date}",
        "",
        "Pre-registered ticker→target hypotheses "
        f"({len(HYPOTHESIS_PAIRS)} pairs), Granger F-tests on monthly "
        f"log-returns at lags 3/6/12, BH-FDR q={base.q_fdr}. "
        f"{base.n_tests} tests run.",
        "",
        "**Granger ≠ causation.** A pass means the ticker's past adds "
        "predictive content beyond the target's own past. Confounders "
        "survive this screen; the Phase-3 forecaster ablation is the "
        "final arbiter. mom12 rows are descriptive cross-correlations "
        "only (overlapping windows invalidate the F-test).",
        "",
        "## Sign check vs the written mechanism",
        "",
        "Every registered pair declares the direction its mechanism "
        "predicts (`EXPECTED_SIGN` in screen.py) or declares itself "
        "two-sided. A Granger F-test is direction-blind, so a pair can "
        "clear BH while moving the OPPOSITE way to its own stated "
        "story — that is not evidence for the hypothesis, and the rows "
        "below are the ones to read first.",
        "",
    ] + _sign_violation_lines(base, sans2020) + [
        "",
        "## Full sample",
        "",
        "| ticker | transform | target | lags | F | p | n | best xcorr | "
        "sign | BH pass | note |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_fmt_candidate_row(c) for c in base.candidates]
    lines += [
        "",
        f"## 2020 excluded (COVID sensitivity) — {sans2020.n_tests} tests",
        "",
        "A signal that only exists because of the 2020 crash is a "
        "one-event artifact, not a relationship.",
        "",
        "| ticker | transform | target | lags | F | p | n | best xcorr | "
        "sign | BH pass | note |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [_fmt_candidate_row(c) for c in sans2020.candidates]
    lines += [
        "",
        "## Annual descriptive leads (NO hypothesis tests — n≈10–15)",
        "",
        "| ticker | target | lead (yrs) | r | n |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {r['ticker']} | {r['target']} | {r['lead_years']} | "
        f"{r['r']:+.3f} | {r['n']} |"
        for r in annual_rows
    ]
    lines += [
        "",
        "## Limitations",
        "",
        "- Granger causality is predictive precedence, not causation.",
        "- Monthly n≈130–250 depending on ticker inception; ZORI starts "
        "2015, JETS 2015, XLRE 2015, FHB 2016.",
        "- Annual-cadence rows are descriptive only and must never be "
        "cited as significant.",
        "- Signals passing here still require the Phase-3 walk-forward "
        "ablation (RMSE improvement + CI90 coverage in [85%, 95%]) "
        "before touching any forecast.",
        "- National-macro predictors (US_*): the screen applies log_return "
        "to whatever it is handed, so for rate-level series (US_MORTGAGE30, "
        "US_DGS10, US_LFPR, US_EMPPOP) the transform is a rough proxy for a "
        "percentage-point change. Fine for predictive precedence; these are "
        "never used as forecast inputs. Labor-participation leads that are "
        "not robust to 2020 exclusion are COVID-coincident (xcorr peaks at "
        "lag 0), not genuine leads.",
        "",
    ]
    return "\n".join(lines)


def build_selected_signals(base: ScreenReport,
                           sans2020: ScreenReport,
                           *, run_date: str) -> dict:
    """BH survivors that ALSO survive the 2020-exclusion re-run."""
    robust_keys = {
        (c.ticker, c.transform, c.target, c.lags)
        for c in sans2020.candidates if c.bh_pass
    }
    signals = []
    for c in base.candidates:
        if not c.bh_pass:
            continue
        key = (c.ticker, c.transform, c.target, c.lags)
        signals.append({
            "name": f"{c.ticker}_{c.transform}_{c.target}_lag{c.lags}".lower(),
            "ticker": c.ticker,
            "transform": c.transform,
            "target": c.target,
            "granger_lags": c.lags,
            "granger_p": c.granger.p_value if c.granger else None,
            "n_obs": c.granger.nobs if c.granger else None,
            "best_xcorr_r": c.best_xcorr.r if c.best_xcorr else None,
            "best_xcorr_lead_months": (
                c.best_xcorr.lead if c.best_xcorr else None),
            "robust_to_2020_exclusion": key in robust_keys,
            # Does the peak correlation run the way the pre-registered
            # mechanism predicts? None = the pair makes no directional
            # claim. False = the hypothesis AS WRITTEN is contradicted,
            # regardless of the p-value — consumers must not promote
            # these without re-stating the mechanism.
            "sign_matches_hypothesis": c.sign_ok,
            "expected_sign": expected_sign(c.ticker, c.target),
            "mean_lead_corr": c.sign_stat,
        })
    return {
        "generated": run_date,
        "screen_version": 1,
        "q_fdr": base.q_fdr,
        "candidates_tested": base.n_tests,
        "signals": signals,
        "limitations": (
            "Granger != causation; selection is FDR-controlled predictive "
            "precedence on monthly data. Only signals passing the Phase-3 "
            "forecaster ablation may influence forecasts."
        ),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the pre-registered market→Hawaii causal screen "
                    "(offline).",
    )
    parser.add_argument("--q", type=float, default=0.10,
                        help="BH false-discovery rate (default 0.10).")
    parser.add_argument("--max-lead", type=int, default=18)
    parser.add_argument("--results-dir", type=Path, default=_RESULTS_DIR)
    parser.add_argument("--signals-out", type=Path,
                        default=_MARKETS_DIR / "selected_signals.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="Run the screen and print, but write nothing.")
    args = parser.parse_args(argv)

    # Fail before any computation if a registered pair has no declared
    # direction: an undeclared pair silently reports sign="n/a" and
    # looks like it passed a check it never ran.
    try:
        assert_sign_coverage()
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    try:
        panel = load_prices_panel()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    tickers = ticker_month_series(panel)
    # Merge national-macro monthly predictors (Phase 2) into the predictor
    # dict; run_screen is shape-agnostic and only tests registered pairs.
    national = load_national_monthly_predictors()
    tickers.update(national)
    if national:
        print(f"[screen] +{len(national)} national predictors: "
              f"{', '.join(sorted(national))}", file=sys.stderr)
    targets = load_monthly_targets()
    missing = [k for k in MONTHLY_TARGETS if k not in targets]
    if missing:
        print(f"::warning::monthly targets unavailable (fetch pending?): "
              f"{', '.join(missing)}", file=sys.stderr)

    base = run_screen(tickers, targets, q=args.q, max_lead=args.max_lead)
    sans2020 = run_screen(tickers, targets, q=args.q,
                          max_lead=args.max_lead, exclude_2020=True)
    annual_rows = annual_descriptive_leads(
        ticker_annual_logdiff(panel), load_annual_targets(), ANNUAL_PAIRS)

    run_date = date.today().isoformat()
    md = render_markdown(base, sans2020, annual_rows, run_date=run_date)
    selected = build_selected_signals(base, sans2020, run_date=run_date)

    n_pass = len(selected["signals"])
    n_robust = sum(1 for s in selected["signals"]
                   if s["robust_to_2020_exclusion"])
    n_wrong = sum(1 for s in selected["signals"]
                  if s.get("sign_matches_hypothesis") is False)
    print(f"[screen] {base.n_tests} Granger tests, {n_pass} BH passes "
          f"({n_robust} robust to 2020 exclusion, "
          f"{n_wrong} with a contradicted sign)", file=sys.stderr)

    # A robust signal that moves opposite to its own mechanism is the
    # one combination nobody should ship on autopilot: it has cleared
    # every statistical gate the screen has while contradicting the
    # story that justified testing it. Not auto-demoted — what counts
    # as "robust" is a documented methodology decision, not something
    # this script should redefine on its own — but it must never pass
    # quietly.
    contradicted_robust = [s for s in selected["signals"]
                           if s["robust_to_2020_exclusion"]
                           and s.get("sign_matches_hypothesis") is False]
    for s in contradicted_robust:
        print(f"::warning:: ROBUST signal contradicts its mechanism: "
              f"{s['ticker']} → {s['target']} lag={s['granger_lags']} "
              f"expected {s['expected_sign']:+d}, mean lead r="
              f"{s['mean_lead_corr']:+.3f}. Do NOT promote to a feature "
              f"channel without re-stating the mechanism.", file=sys.stderr)
    top = sorted(
        (c for c in base.candidates if c.granger),
        key=lambda c: c.granger.p_value)[:10]
    for c in top:
        print(f"[screen]   {c.ticker:>5} → {c.target:<16} lags={c.lags:<3}"
              f"p={c.granger.p_value:.4f} "
              f"{'BH-PASS' if c.bh_pass else ''}", file=sys.stderr)

    if args.dry_run:
        print("[screen] dry-run: nothing written", file=sys.stderr)
        return 0

    args.results_dir.mkdir(parents=True, exist_ok=True)
    md_path = args.results_dir / f"market_signal_screen_{run_date}.md"
    md_path.write_text(md)
    _atomic_write_json(args.signals_out, selected)
    print(f"[screen] wrote {md_path}", file=sys.stderr)
    print(f"[screen] wrote {args.signals_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
