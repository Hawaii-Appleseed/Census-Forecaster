#!/usr/bin/env python3
"""
cpi_projection_walkforward.py
-----------------------------
Walk-forward (pseudo-out-of-sample) backtest of the CPI forward-projection
implemented in `pipelines/grocery/src/price_adjuster.py::project_forward_full`.

Methodology
~~~~~~~~~~~
For each Honolulu BLS CPI series in BACKTEST_SERIES, enumerate every
observed bimonthly print T from MIN_ANCHOR_YEAR onward (skipping the last
LEAD_MONTHS to leave room for ground-truth at horizon h). For each anchor:

    1. Truncate the series to points <= T.
    2. For every later observation T' (still on the bimonthly grid, so
       horizons fall on h = 2, 4, 6 months), compute:
         a. Projected CPI at T' via project_forward_full(...).
         b. Compare to the actual print at T'.
    3. Record (series, anchor, horizon, projected, actual, ci90_lo,
       ci90_hi, cap_fired, monthly_rate_used) for every cell in the
       sweep grid: phi × cap × window × {recency-weighted, gap-weighted}.

Aggregation: per (cell, series, horizon) compute n, MAPE, medianAPE,
max APE, RMSE-pct, signed bias, CI90 coverage.

Outputs
~~~~~~~
`backtests/results/cpi_projection_<run-date>.md`
    Headline tables per series (live default cell starred), horizon
    decomposition, best-cell-per-series, calibration recommendation,
    optional gap-weighted variant comparison.

Important caveats
~~~~~~~~~~~~~~~~~
1. **BLS revisions silently bias accuracy upward.** The public API only
   exposes the latest-revised series; the harness slices it as-of T but
   uses the revised value at T, not the value that would have been live
   then. Treat reported MAPE as a *lower bound* on true live error.

2. **No regime-aware splitting.** A single 2020-2025 walk-forward spans
   one inflation cycle (2021 spike, 2023-25 disinflation). Constants
   tuned here may drift in a different regime. Use the --calibrate
   --train-test mode for an out-of-fold sanity check.

Run
~~~
    python3 backtests/cpi_projection_walkforward.py
    python3 backtests/cpi_projection_walkforward.py --calibrate
    python3 backtests/cpi_projection_walkforward.py --variant gap-weighted
    python3 backtests/cpi_projection_walkforward.py --no-cache
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..bls.client import fetch_cpi_data

# ---------------------------------------------------------------------------
# Imports from the live projector
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Imports from the live projector (in-package)
# ---------------------------------------------------------------------------
from ..bls.projection import (
    _PROJ_SE_INFLATOR,
    _RESIDUAL_LOG_STD_PRIOR,
    _Z_90,
    PROJ_DAMPING,
    PROJ_MONTHLY_CAP,
    ProjectionResult,
    damped_compound_factor,
    forecast_se_log,
    project_forward_full,
    residual_log_std,
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
CACHE_DIR   = Path(os.environ.get("CENSUS_FORECASTER_BACKTEST_DIR", str(Path.cwd() / "backtests"))) / "cache" / "cpi"
RESULTS_DIR = Path(os.environ.get("CENSUS_FORECASTER_BACKTEST_DIR", str(Path.cwd() / "backtests"))) / "results"

# Series scope. Urban Hawaii CPI (S49F, published from 2017; all-items
# bimonthly on odd months). CORRECTED 2026-07-27: the previous S49A IDs
# were labelled Honolulu but are Los Angeles per the BLS API catalog —
# the κ=1.50 provenance run ("5-series × 63-anchor") was therefore an
# LA panel at monthly cadence. See census_forecaster.bls.panel.
BACKTEST_SERIES = [
    "CUURS49FSAF11",   # food at home
    "CUURS49FSEHA",    # rent of primary residence
    "CUURS49FSA0",     # all items
    "CUURS49FSEFV",    # fruits and vegetables
    "CUURS49FSETB01",  # gasoline (high-volatility stress test)
]
SERIES_LABEL = {
    "CUURS49FSAF11":  "food-at-home",
    "CUURS49FSEHA":   "rent",
    "CUURS49FSA0":    "all-items",
    "CUURS49FSEFV":   "fruits-veg",
    "CUURS49FSETB01": "gasoline",
}

START_YEAR = 2018
END_YEAR   = date.today().year

MIN_ANCHOR_YEAR = 2020
LEAD_MONTHS     = 12   # skip the last N months of history so each anchor
                       # has at least h=6 months of ground truth

# Horizons reported in aggregate; raw rows may include any observed h.
REPORT_HORIZONS = [2, 4, 6]

# Sweep grid
SWEEP_PHIS    = [0.80, 0.85, 0.90, 0.92, 0.95, 1.0]
SWEEP_CAPS    = [0.010, 0.015, 0.0189, 0.025, math.inf]
SWEEP_WINDOWS = [2, 3, 4, None]   # None = use all pairs

# Production / live default cell — used for headline / starred row /
# calibration. Must match price_adjuster module constants.
LIVE_PHI    = PROJ_DAMPING
LIVE_CAP    = PROJ_MONTHLY_CAP
LIVE_WINDOW = None
LIVE_INFLATOR = _PROJ_SE_INFLATOR


# ---------------------------------------------------------------------------
# Data fetch + caching
# ---------------------------------------------------------------------------
def _cache_path(series_id: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{series_id}_{START_YEAR}_{END_YEAR}.json"


def fetch_series_cached(series_id: str, use_cache: bool = True) -> list[dict]:
    """Return a sorted list of {year, period, value} dicts for one series.

    Cached at `backtests/cache/cpi/<series>_<start>_<end>.json` so reruns
    don't re-hit the BLS public API. Use --no-cache to force a refetch.

    NB: separate cache directory from the production grocery pipeline's
    `pipelines/grocery/data/cpi_cache/` to avoid collision.
    """
    path = _cache_path(series_id)
    if use_cache and path.exists():
        return json.loads(path.read_text())
    print(f"  Fetching BLS series {series_id} ({START_YEAR}-{END_YEAR})…")
    data = fetch_cpi_data([series_id], START_YEAR, END_YEAR)
    points = data.get(series_id, [])
    path.write_text(json.dumps(points, indent=2))
    return points


def points_to_iso(points: list[dict]) -> list[str]:
    """Return chronologically-sorted ISO 'YYYY-MM' strings for each point."""
    return [f"{p['year']}-{int(p['period'][1:]):02d}" for p in points]


# ---------------------------------------------------------------------------
# Anchor / horizon enumeration
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Anchor:
    """One anchor in the walk-forward: data live up to and including idx."""
    series_id: str
    idx: int            # index into the full sorted points list
    year: int
    month: int          # 1, 3, 5, 7, 9, 11

    @property
    def iso(self) -> str:
        return f"{self.year}-{self.month:02d}"


def enumerate_anchors(series_id: str, points: list[dict]) -> list[Anchor]:
    """List anchors satisfying: year >= MIN_ANCHOR_YEAR and at least
    LEAD_MONTHS of remaining history past the anchor.

    Bimonthly cadence — odd months only. We enumerate them by index since
    the slicing operation in the harness is index-based.
    """
    if not points:
        return []
    last = points[-1]
    last_y, last_m = last["year"], int(last["period"][1:])
    last_total = last_y * 12 + last_m

    anchors: list[Anchor] = []
    for i, p in enumerate(points):
        y, m = p["year"], int(p["period"][1:])
        if y < MIN_ANCHOR_YEAR:
            continue
        if (last_total - (y * 12 + m)) < LEAD_MONTHS:
            break
        anchors.append(Anchor(series_id=series_id, idx=i, year=y, month=m))
    return anchors


def horizon_observations(points: list[dict], anchor: Anchor) -> list[tuple[int, dict]]:
    """For an anchor, return (h_months, observation) pairs for every later
    bimonthly print up to h <= max(REPORT_HORIZONS).

    Each pair is a ground-truth datapoint for the projector at horizon h.
    """
    out = []
    a_total = anchor.year * 12 + anchor.month
    max_h = max(REPORT_HORIZONS)
    for j in range(anchor.idx + 1, len(points)):
        p = points[j]
        h = (p["year"] * 12 + int(p["period"][1:])) - a_total
        if h <= 0:
            continue
        if h > max_h:
            break
        out.append((h, p))
    return out


# ---------------------------------------------------------------------------
# Projection variants
# ---------------------------------------------------------------------------
def _smoothed_monthly_rate_gap_weighted(
    ordered: list[dict],
    max_pairs: int | None = None,
) -> float | None:
    """Variant smoother: weight ∝ (recency_decay × months_between).

    Inverse-variance pooling: under iid log-return noise, a per-month rate
    computed over k months has variance σ²/k, so longer-gap pairs deserve
    *higher* weight. The production smoother uses recency only.
    """
    if max_pairs is not None and max_pairs > 0 and len(ordered) > max_pairs + 1:
        ordered = ordered[-(max_pairs + 1):]

    pairs: list[tuple[float, float]] = []
    for i in range(1, len(ordered)):
        prev = ordered[i - 1]
        curr = ordered[i]
        prev_y, prev_m = prev["year"], int(prev["period"][1:])
        curr_y, curr_m = curr["year"], int(curr["period"][1:])
        months_between = (curr_y - prev_y) * 12 + (curr_m - prev_m)
        if months_between <= 0 or prev["value"] <= 0:
            continue
        monthly_rate = (curr["value"] / prev["value"]) ** (1.0 / months_between) - 1.0
        weight = (0.5 ** (len(ordered) - 1 - i)) * months_between
        pairs.append((monthly_rate, weight))

    if not pairs:
        return None
    return sum(r * w for r, w in pairs) / sum(w for _, w in pairs)


def project_full_with_variant(
    points: list[dict],
    target_date: date,
    *,
    phi: float,
    cap: float,
    max_pairs: int | None,
    inflator: float,
    variant: str = "recency",
) -> ProjectionResult:
    """Wrapper that swaps the smoother for the gap-weighted variant when
    `variant="gap-weighted"`. Production uses "recency"."""
    if variant == "recency":
        return project_forward_full(points, target_date,
                                     phi=phi, cap=cap, max_pairs=max_pairs,
                                     inflator=inflator)
    if variant != "gap-weighted":
        raise ValueError(f"unknown variant: {variant!r}")

    # Re-implement the projection inline with the alt smoother.
    if not points:
        raise ValueError("cannot project forward from empty series")
    ordered = sorted(points, key=lambda p: (p["year"], int(p["period"][1:])))
    latest = ordered[-1]
    latest_value = float(latest["value"])
    latest_y, latest_m = latest["year"], int(latest["period"][1:])
    months_beyond = (target_date.year - latest_y) * 12 + (target_date.month - latest_m)
    if months_beyond <= 0 or len(ordered) < 2:
        return ProjectionResult(value=latest_value, monthly_rate=0.0, cap_fired=False,
                                residual_log_std=_RESIDUAL_LOG_STD_PRIOR,
                                forecast_se_log=0.0, point_uncapped=latest_value,
                                horizon_months=max(months_beyond, 0))
    smoothed = _smoothed_monthly_rate_gap_weighted(ordered, max_pairs=max_pairs)
    if smoothed is None:
        return ProjectionResult(value=latest_value, monthly_rate=0.0, cap_fired=False,
                                residual_log_std=_RESIDUAL_LOG_STD_PRIOR,
                                forecast_se_log=0.0, point_uncapped=latest_value,
                                horizon_months=months_beyond)
    capped = max(min(smoothed, cap), -cap)
    cap_fired = (capped != smoothed)
    point = latest_value * damped_compound_factor(capped, months_beyond, phi=phi)
    point_unc = latest_value * damped_compound_factor(smoothed, months_beyond, phi=phi)
    residual_std = residual_log_std(ordered, capped, max_pairs=max_pairs)
    se_log = forecast_se_log(residual_std, months_beyond, phi=phi, inflator=inflator)
    return ProjectionResult(value=float(point), monthly_rate=float(capped),
                            cap_fired=bool(cap_fired),
                            residual_log_std=float(residual_std),
                            forecast_se_log=float(se_log),
                            point_uncapped=float(point_unc),
                            horizon_months=int(months_beyond))


# ---------------------------------------------------------------------------
# Cell evaluation
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Cell:
    phi: float
    cap: float
    window: int | None
    inflator: float = LIVE_INFLATOR
    variant: str = "recency"

    @property
    def label(self) -> str:
        cap_str = "∞" if math.isinf(self.cap) else f"{self.cap:.4f}"
        win_str = "all" if self.window is None else str(self.window)
        return f"φ={self.phi:.2f} cap={cap_str} win={win_str}"


@dataclass
class BacktestRow:
    series_id: str
    anchor_iso: str
    target_iso: str
    horizon: int
    cell_label: str
    actual: float
    projected: float
    ci90_lo: float
    ci90_hi: float
    cap_fired: bool
    monthly_rate: float

    @property
    def abs_pct_err(self) -> float:
        if self.actual == 0:
            return float("inf")
        return abs(self.projected - self.actual) / abs(self.actual)

    @property
    def signed_pct_err(self) -> float:
        if self.actual == 0:
            return float("inf")
        return (self.projected - self.actual) / abs(self.actual)

    @property
    def covered_by_ci90(self) -> bool:
        return self.ci90_lo <= self.actual <= self.ci90_hi


def evaluate_cell(
    series_id: str,
    points: list[dict],
    anchor: Anchor,
    h: int,
    target_obs: dict,
    cell: Cell,
) -> BacktestRow | None:
    """Project from anchor to anchor+h via cell config; compare to target_obs."""
    truncated = points[: anchor.idx + 1]
    if len(truncated) < 2:
        return None
    target_y = target_obs["year"]
    target_m = int(target_obs["period"][1:])
    proj = project_full_with_variant(
        truncated, date(target_y, target_m, 1),
        phi=cell.phi, cap=cell.cap, max_pairs=cell.window,
        inflator=cell.inflator, variant=cell.variant,
    )
    actual = float(target_obs["value"])
    # log-space CI on the *level* (the projected CPI value).
    ci_lo = proj.value * math.exp(-_Z_90 * proj.forecast_se_log) if proj.forecast_se_log > 0 else proj.value
    ci_hi = proj.value * math.exp(+_Z_90 * proj.forecast_se_log) if proj.forecast_se_log > 0 else proj.value
    if proj.cap_fired and proj.point_uncapped > proj.value:
        ci_hi = max(ci_hi, proj.point_uncapped)
    return BacktestRow(
        series_id=series_id,
        anchor_iso=anchor.iso,
        target_iso=f"{target_y}-{target_m:02d}",
        horizon=h,
        cell_label=cell.label,
        actual=actual,
        projected=proj.value,
        ci90_lo=ci_lo,
        ci90_hi=ci_hi,
        cap_fired=proj.cap_fired,
        monthly_rate=proj.monthly_rate,
    )


# ---------------------------------------------------------------------------
# Sweep + aggregation
# ---------------------------------------------------------------------------
def run_sweep(
    series_data: dict[str, list[dict]],
    cells: list[Cell],
) -> list[BacktestRow]:
    rows: list[BacktestRow] = []
    for series_id, points in series_data.items():
        anchors = enumerate_anchors(series_id, points)
        if not anchors:
            print(f"  WARNING: no anchors for {series_id}")
            continue
        n_evals = 0
        for anchor in anchors:
            future = horizon_observations(points, anchor)
            for h, obs in future:
                if h not in REPORT_HORIZONS:
                    continue
                for cell in cells:
                    row = evaluate_cell(series_id, points, anchor, h, obs, cell)
                    if row is not None:
                        rows.append(row)
                        n_evals += 1
        print(f"  {SERIES_LABEL.get(series_id, series_id)}: "
              f"{len(anchors)} anchors × {len(REPORT_HORIZONS)} horizons × "
              f"{len(cells)} cells = {n_evals} rows")
    return rows


def aggregate(rows: list[BacktestRow]) -> dict:
    """Group rows by (cell_label, series_id, horizon) and compute summary stats."""
    by_key: dict[tuple, list[BacktestRow]] = {}
    for r in rows:
        key = (r.cell_label, r.series_id, r.horizon)
        by_key.setdefault(key, []).append(r)

    summary: dict[tuple, dict] = {}
    for key, group in by_key.items():
        ape = [r.abs_pct_err for r in group if math.isfinite(r.abs_pct_err)]
        spe = [r.signed_pct_err for r in group if math.isfinite(r.signed_pct_err)]
        cov = [r.covered_by_ci90 for r in group]
        cap_fired_n = sum(1 for r in group if r.cap_fired)
        summary[key] = {
            "n":           len(group),
            "mape":        statistics.mean(ape) if ape else float("nan"),
            "median_ape":  statistics.median(ape) if ape else float("nan"),
            "max_ape":     max(ape) if ape else float("nan"),
            "rmse_pct":    math.sqrt(statistics.mean([e * e for e in spe])) if spe else float("nan"),
            "bias":        statistics.mean(spe) if spe else float("nan"),
            "ci90_cov":    sum(cov) / len(cov) if cov else float("nan"),
            "cap_fired_n": cap_fired_n,
        }
    return summary


def aggregate_by_cell(rows: list[BacktestRow]) -> dict:
    """Aggregate stats across all (series, horizon) combinations per cell."""
    by_cell: dict[str, list[BacktestRow]] = {}
    for r in rows:
        by_cell.setdefault(r.cell_label, []).append(r)
    out: dict[str, dict] = {}
    for cell_label, group in by_cell.items():
        ape = [r.abs_pct_err for r in group if math.isfinite(r.abs_pct_err)]
        spe = [r.signed_pct_err for r in group if math.isfinite(r.signed_pct_err)]
        cov = [r.covered_by_ci90 for r in group]
        out[cell_label] = {
            "n":          len(group),
            "mape":       statistics.mean(ape) if ape else float("nan"),
            "median_ape": statistics.median(ape) if ape else float("nan"),
            "max_ape":    max(ape) if ape else float("nan"),
            "rmse_pct":   math.sqrt(statistics.mean([e * e for e in spe])) if spe else float("nan"),
            "bias":       statistics.mean(spe) if spe else float("nan"),
            "ci90_cov":   sum(cov) / len(cov) if cov else float("nan"),
        }
    return out


# ---------------------------------------------------------------------------
# Calibration: choose inflator κ for live cell so CI90 coverage ≈ 0.90
# ---------------------------------------------------------------------------
def calibrate_inflator(
    series_data: dict[str, list[dict]],
    *,
    grid: Iterable[float] = tuple(round(1.0 + 0.1 * i, 2) for i in range(11)),  # 1.0 .. 2.0
) -> tuple[float, float, float]:
    """Search inflator κ in `grid` for the value yielding CI90 coverage
    closest to 0.90 on the live cell (phi, cap, window) = LIVE_*.

    Returns (best_kappa, best_coverage, baseline_coverage_at_kappa_1).
    """
    print("\n[calibrate] sweeping inflator κ on live cell …")
    best_kappa = 1.0
    best_diff  = float("inf")
    best_cov   = float("nan")
    baseline_cov = float("nan")

    for kappa in grid:
        cell = Cell(phi=LIVE_PHI, cap=LIVE_CAP, window=LIVE_WINDOW,
                    inflator=kappa, variant="recency")
        rows = run_sweep(series_data, [cell])
        cov = aggregate_by_cell(rows)[cell.label]["ci90_cov"]
        if abs(kappa - 1.0) < 1e-9:
            baseline_cov = cov
        diff = abs(cov - 0.90)
        marker = ""
        if diff < best_diff:
            best_diff = diff
            best_kappa = kappa
            best_cov = cov
            marker = "  ← best so far"
        print(f"  κ={kappa:.2f}: coverage={cov:.3f}{marker}")
    return best_kappa, best_cov, baseline_cov


# ---------------------------------------------------------------------------
# Markdown reporting
# ---------------------------------------------------------------------------
def fmt_pct(x: float) -> str:
    return "n/a" if not math.isfinite(x) else f"{x * 100:.2f}%"


def fmt_signed_pct(x: float) -> str:
    return "n/a" if not math.isfinite(x) else f"{x * 100:+.2f}%"


def render_markdown_report(
    series_data: dict[str, list[dict]],
    rows: list[BacktestRow],
    cells: list[Cell],
    live_cell: Cell,
    *,
    calibration: tuple[float, float, float] | None = None,
    variant_compare: dict | None = None,
    asof: str,
) -> str:
    summary  = aggregate(rows)
    by_cell  = aggregate_by_cell(rows)

    out: list[str] = []
    out.append(f"# CPI projection walk-forward backtest — {asof}")
    out.append("")
    out.append("Walk-forward (pseudo-out-of-sample) evaluation of the CPI "
               "forward-projection in `pipelines/grocery/src/price_adjuster.py`. "
               "For each series, every observed bimonthly print since "
               f"{MIN_ANCHOR_YEAR}-01 (with at least {LEAD_MONTHS} months of "
               "lead time for ground truth) is used as an anchor; the "
               "projection is compared to the actual print at horizon h ∈ "
               f"{REPORT_HORIZONS}.")
    out.append("")
    out.append("**Caveat:** BLS revises CPI prints occasionally. The public "
               "API exposes only the latest-revised series, so the harness "
               "uses revised values at T even though they would not have been "
               "live then. Treat reported MAPE as a *lower bound* on true live error.")
    out.append("")

    # Per-series anchor counts
    out.append("## Coverage")
    out.append("")
    out.append("| Series | Anchors | Horizon-rows |")
    out.append("|---|---:|---:|")
    for sid in BACKTEST_SERIES:
        anc = enumerate_anchors(sid, series_data.get(sid, []))
        sid_rows = [r for r in rows if r.series_id == sid]
        rows_per_cell = (len(sid_rows) // len(cells)) if cells else 0
        out.append(f"| {SERIES_LABEL.get(sid, sid)} | {len(anc)} | {rows_per_cell} |")
    out.append("")

    # Headline: live cell vs. best cell, per series, aggregated over horizons
    out.append("## Headline: live default cell vs. best per-series cell")
    out.append("")
    out.append("Aggregated across all horizons. Live cell: "
               f"`φ={live_cell.phi:.2f} cap={live_cell.cap:.4f} win=all`.")
    out.append("")
    out.append("| Series | Live MAPE | Live CI90 cov | Best cell | Best MAPE | Δ MAPE |")
    out.append("|---|---:|---:|---|---:|---:|")
    for sid in BACKTEST_SERIES:
        sid_rows = [r for r in rows if r.series_id == sid]
        sid_by_cell: dict[str, list[BacktestRow]] = {}
        for r in sid_rows:
            sid_by_cell.setdefault(r.cell_label, []).append(r)
        if not sid_by_cell:
            continue
        sid_summary = {
            label: {
                "mape": statistics.mean([r.abs_pct_err for r in g if math.isfinite(r.abs_pct_err)]),
                "cov":  sum(r.covered_by_ci90 for r in g) / len(g),
            }
            for label, g in sid_by_cell.items()
        }
        live_label = live_cell.label
        live_stats = sid_summary.get(live_label, {"mape": float("nan"), "cov": float("nan")})
        best_label, best_stats = min(sid_summary.items(), key=lambda kv: kv[1]["mape"])
        delta = best_stats["mape"] - live_stats["mape"]
        out.append(
            f"| {SERIES_LABEL.get(sid, sid)} | "
            f"{fmt_pct(live_stats['mape'])} | {fmt_pct(live_stats['cov'])} | "
            f"`{best_label}` | {fmt_pct(best_stats['mape'])} | "
            f"{fmt_signed_pct(delta)} |"
        )
    out.append("")
    out.append("**Δ MAPE** is `best − live`; negative means the best cell improves on live.")
    out.append("")

    # Per-horizon breakdown of live cell
    out.append(f"## Live cell `{live_cell.label}` — horizon decomposition")
    out.append("")
    out.append("| Series | Horizon | n | MAPE | medianAPE | maxAPE | bias | CI90 cov |")
    out.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for sid in BACKTEST_SERIES:
        for h in REPORT_HORIZONS:
            key = (live_cell.label, sid, h)
            s = summary.get(key)
            if s is None:
                continue
            out.append(
                f"| {SERIES_LABEL.get(sid, sid)} | {h}mo | {s['n']} | "
                f"{fmt_pct(s['mape'])} | {fmt_pct(s['median_ape'])} | "
                f"{fmt_pct(s['max_ape'])} | {fmt_signed_pct(s['bias'])} | "
                f"{fmt_pct(s['ci90_cov'])} |"
            )
    out.append("")

    # Top-5 cells overall (across-series MAPE)
    out.append("## Top 5 sweep cells (across all series + horizons)")
    out.append("")
    sorted_cells = sorted(by_cell.items(), key=lambda kv: kv[1]["mape"])
    out.append("| Cell | n | MAPE | medianAPE | bias | CI90 cov |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for label, s in sorted_cells[:5]:
        marker = " **(live)**" if label == live_cell.label else ""
        out.append(
            f"| `{label}`{marker} | {s['n']} | {fmt_pct(s['mape'])} | "
            f"{fmt_pct(s['median_ape'])} | {fmt_signed_pct(s['bias'])} | "
            f"{fmt_pct(s['ci90_cov'])} |"
        )
    out.append("")

    # Live cell rank
    sorted_labels = [label for label, _ in sorted_cells]
    if live_cell.label in sorted_labels:
        rank = sorted_labels.index(live_cell.label) + 1
        live_mape = by_cell[live_cell.label]["mape"]
        best_mape = sorted_cells[0][1]["mape"]
        gap = (live_mape - best_mape) / best_mape if best_mape > 0 else float("nan")
        out.append(f"**Live cell rank:** #{rank} of {len(sorted_cells)}; "
                   f"MAPE gap to best = {fmt_pct(gap)}.")
        out.append("")

    # Calibration recommendation
    if calibration is not None:
        best_kappa, best_cov, baseline_cov = calibration
        out.append("## Calibration")
        out.append("")
        out.append(f"`_PROJ_SE_INFLATOR` calibration: starting at κ=1.0 the "
                   f"live cell achieves CI90 coverage of {fmt_pct(baseline_cov)}. "
                   f"Sweeping κ ∈ [1.0, 2.0] in 0.1 steps, the value closest to "
                   f"the 90% target is **κ = {best_kappa:.2f}** "
                   f"(coverage = {fmt_pct(best_cov)}).")
        out.append("")
        if abs(best_kappa - 1.0) < 1e-9 or abs(best_cov - baseline_cov) < 0.02:
            out.append("**Recommendation:** the formula SE is already "
                       "well-calibrated; leave `_PROJ_SE_INFLATOR = 1.0`.")
        else:
            out.append(f"**Recommendation:** bump `_PROJ_SE_INFLATOR` to "
                       f"`{best_kappa:.2f}` and document derivation in "
                       f"METHODOLOGY.md. Re-derivation command: "
                       f"`python3 backtests/cpi_projection_walkforward.py --calibrate`.")
        out.append("")

    # Variant comparison
    if variant_compare is not None:
        out.append("## Gap-weighted variant comparison")
        out.append("")
        out.append("| Variant | MAPE | medianAPE | bias | CI90 cov |")
        out.append("|---|---:|---:|---:|---:|")
        for label, s in variant_compare.items():
            out.append(
                f"| {label} | {fmt_pct(s['mape'])} | {fmt_pct(s['median_ape'])} | "
                f"{fmt_signed_pct(s['bias'])} | {fmt_pct(s['ci90_cov'])} |"
            )
        out.append("")
        recency_mape = variant_compare.get("recency-weighted (live)", {}).get("mape")
        gw_mape = variant_compare.get("gap-weighted", {}).get("mape")
        if recency_mape and gw_mape and math.isfinite(recency_mape) and math.isfinite(gw_mape):
            improvement = (recency_mape - gw_mape) / recency_mape
            if improvement >= 0.05:
                out.append(f"**Recommendation:** gap-weighted reduces MAPE by "
                           f"{improvement * 100:.1f}% — adopt and update the smoother in "
                           f"`pipelines/grocery/src/price_adjuster.py::_smoothed_monthly_rate`.")
            else:
                out.append(f"**Recommendation:** gap-weighted variant differs "
                           f"by {improvement * 100:+.1f}% — below the 5% threshold; "
                           f"keep the existing recency-only smoother.")
        out.append("")

    # Footer
    out.append("---")
    out.append(f"_Generated by `backtests/cpi_projection_walkforward.py` on {asof}._")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def build_cells(variant: str = "recency",
                inflator: float = LIVE_INFLATOR) -> list[Cell]:
    cells: list[Cell] = []
    for phi in SWEEP_PHIS:
        for cap in SWEEP_CAPS:
            for window in SWEEP_WINDOWS:
                cells.append(Cell(phi=phi, cap=cap, window=window,
                                  inflator=inflator, variant=variant))
    return cells


def _run_stratified_v3(asof: str) -> int:
    """v3 stratified BLS calibration entry point.

    Loads the bundled multi-MSA panel from `data/bls_panel/cpi_panel.json`,
    runs `run_stratified_bls_calibration`, writes the result to
    `data/anchors/bls_calibration.json`, and emits a per-stratum
    Markdown summary into the backtest results directory.

    Returns 0 on success, non-zero on failure.
    """
    from pathlib import Path

    from ..bls.calibration import run_stratified_bls_calibration, write_bls_calibration

    pkg_root = Path(__file__).resolve().parent.parent
    panel_path = pkg_root / "data" / "bls_panel" / "cpi_panel.json"
    if not panel_path.exists():
        print(
            f"ERROR: bundled panel not found at {panel_path}. Run "
            "`BLS_API_KEY=<key> python -m census_forecaster.scripts.refresh_bls_panel` "
            "first.", file=sys.stderr,
        )
        return 2

    with open(panel_path) as f:
        panel = json.load(f)
    cpi_data = panel["series"]

    print(f"Running v3 stratified BLS calibration on {len(cpi_data)} series …")
    payload = run_stratified_bls_calibration(
        cpi_data=cpi_data,
        horizons_months=(2, 4, 6, 8, 12),
        n_threshold=20,
    )
    print(
        f"  {len(payload['fold_residuals'])} folds across "
        f"{len(payload['strata_records']['se_inflator'])} strata cells"
    )

    out_path = pkg_root / "data" / "anchors" / "bls_calibration.json"
    write_bls_calibration(payload, out_path)
    print(f"Wrote {out_path}")

    # Per-stratum diagnostic Markdown
    md_lines = [
        f"# BLS v3 stratified calibration — {asof}\n\n",
        f"**Schema:** v{payload['schema_version']}\n",
        f"**Run date:** {payload['run_date']}\n",
        f"**Horizons (months):** {payload['horizons_months']}\n",
        f"**Total folds:** {len(payload['fold_residuals'])}\n",
        f"**Strata cells:** {len(payload['strata_records']['se_inflator'])}\n\n",
        "## SE inflators with non-trivial κ (n ≥ 20, |κ−1| > 0.1)\n\n",
        "| Series | h_bucket | vol_regime | κ | n | cov_pre → cov_post |\n",
        "|---|---|---|---:|---:|---|\n",
    ]
    notable = [
        r for r in payload["strata_records"]["se_inflator"]
        if abs(r["value"] - 1.0) > 0.1
        and r["n_folds"] >= 20
        and r["pop_bucket"] != "*"
    ]
    notable.sort(key=lambda r: (r["pop_bucket"], r["h_bucket"]))
    for r in notable:
        h, vol = r["h_bucket"].split("|", 1)
        cov_pre = r["extra"].get("coverage_pre", 0.0)
        cov_post = r["extra"].get("coverage_post", 0.0)
        md_lines.append(
            f"| {r['pop_bucket']} | {h} | {vol} | {r['value']:.3f} | "
            f"{r['n_folds']} | {cov_pre*100:.0f}% → {cov_post*100:.0f}% |\n"
        )
    md_lines.append("\n## Bias corrections (applied b ≠ 0, n ≥ 20)\n\n")
    md_lines.append("| Series | h_bucket | vol_regime | b (log) | b (pct) | n |\n")
    md_lines.append("|---|---|---|---:|---:|---:|\n")
    bias_records = sorted(
        [r for r in payload["strata_records"]["bias"]
         if r["value"] != 0.0 and r["n_folds"] >= 20 and r["pop_bucket"] != "*"],
        key=lambda r: (r["pop_bucket"], r["h_bucket"]),
    )
    for r in bias_records:
        h, vol = r["h_bucket"].split("|", 1)
        pct = (math.exp(r["value"]) - 1) * 100
        md_lines.append(
            f"| {r['pop_bucket']} | {h} | {vol} | {r['value']:+.4f} | "
            f"{pct:+.2f}% | {r['n_folds']} |\n"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    md_path = RESULTS_DIR / f"bls_v3_calibration_{asof}.md"
    md_path.write_text("".join(md_lines))
    print(f"Wrote {md_path}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-cache", action="store_true",
                    help="Force refetch of BLS series; otherwise use backtests/cache/cpi/.")
    ap.add_argument("--calibrate", action="store_true",
                    help="Sweep _PROJ_SE_INFLATOR κ to target CI90 coverage 0.90 (v2 legacy).")
    ap.add_argument("--stratify", action="store_true",
                    help=("Run v3 stratified BLS calibration on the bundled multi-MSA "
                          "panel and write data/anchors/bls_calibration.json."))
    ap.add_argument("--variant", choices=["recency", "gap-weighted", "compare"],
                    default="recency",
                    help="Smoother variant to use. 'compare' runs both and reports delta.")
    ap.add_argument("--asof", default=date.today().isoformat(),
                    help="Date stamp for the report filename. Default: today.")
    args = ap.parse_args()

    if args.stratify:
        return _run_stratified_v3(args.asof)

    use_cache = not args.no_cache

    print(f"Loading {len(BACKTEST_SERIES)} BLS series …")
    series_data: dict[str, list[dict]] = {}
    for sid in BACKTEST_SERIES:
        try:
            series_data[sid] = fetch_series_cached(sid, use_cache=use_cache)
        except Exception as exc:  # noqa: BLE001
            print(f"  WARNING: failed to fetch {sid}: {exc}")
            series_data[sid] = []

    if not any(series_data.values()):
        print("ERROR: no series data available; aborting.")
        return 1

    print()
    live_cell = Cell(phi=LIVE_PHI, cap=LIVE_CAP, window=LIVE_WINDOW,
                     inflator=LIVE_INFLATOR, variant="recency")

    print("Running sweep grid …")
    cells = build_cells(variant=args.variant if args.variant != "compare" else "recency")
    rows = run_sweep(series_data, cells)
    print(f"  Total rows: {len(rows)} ({len(cells)} cells × series × horizons)")

    calibration = None
    if args.calibrate:
        calibration = calibrate_inflator(series_data)

    variant_compare = None
    if args.variant == "compare":
        print("\nRunning gap-weighted variant on live cell …")
        gw_cell = Cell(phi=LIVE_PHI, cap=LIVE_CAP, window=LIVE_WINDOW,
                       inflator=LIVE_INFLATOR, variant="gap-weighted")
        rec_rows = [r for r in rows if r.cell_label == live_cell.label]
        gw_rows = run_sweep(series_data, [gw_cell])
        # rename labels for clarity
        rec_label = "recency-weighted (live)"
        gw_label = "gap-weighted"
        agg_rec = aggregate_by_cell(rec_rows).get(live_cell.label, {})
        agg_gw = aggregate_by_cell(gw_rows).get(gw_cell.label, {})
        variant_compare = {rec_label: agg_rec, gw_label: agg_gw}

    asof = args.asof
    md = render_markdown_report(
        series_data=series_data, rows=rows, cells=cells,
        live_cell=live_cell, calibration=calibration,
        variant_compare=variant_compare, asof=asof,
    )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS_DIR / f"cpi_projection_{asof}.md"
    out_path.write_text(md)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
