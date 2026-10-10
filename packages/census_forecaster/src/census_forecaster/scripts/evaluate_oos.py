"""Out-of-sample check of the shipped ACS calibration on 2023-2024 truth.

The bundled calibration (`data/anchors/calibration.json`) is fit on
walk-forward folds anchored 2014-2022. The bundled panel carries 1-year
ACS truth through 2024, so anchor 2023 is a year the calibration never
used *as an anchor*. This script projects from anchors 2022 and 2023
through the production path (`project_ensemble_multi`, shipped defaults:
ML on, Kalman per allowlist, publication as-of information set) and
scores the blended forecast against the 2023/2024 1-year prints.

Two caveats the report states explicitly, because "never-touched" is
not quite true:

* Anchor 2022 h=1,2 (targets 2023, 2024) **is** the generator's
  evaluation set (`calibration.json["evaluation_coverage"]`), so those
  folds are held-out but not unseen. What this script adds over the
  generator's own report is the *blended* production forecast — the
  generator scores each ensemble member separately and never the
  ensemble itself — and the anchor-2023 → 2024 fold.
* The 2023 and 2024 prints also serve as truth for long-horizon tuning
  folds (anchor 2019 h=4/5, anchor 2020 h=3/4, anchor 2021 h=2/3), so the
  κ / bias / conformal records were fit on residuals that include these
  target years. `--masked` regenerates the calibration in-process with
  every observation after `--mask-year` removed from the panel (anchors
  restricted accordingly) and evaluates the same folds against it; that
  is the strictly out-of-sample number.

Usage
-----
    python -m census_forecaster.scripts.evaluate_oos
    python -m census_forecaster.scripts.evaluate_oos --anchors 2022,2023 --max-truth-year 2024
    python -m census_forecaster.scripts.evaluate_oos --masked --mask-year 2022
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from common.models import AcsObservation
from common.publication import acs_1y_release_date

from ..acs.calibration import _truncate
from ..acs.projection import effective_year
from ..acs.strata import WILDCARD, classify_pop

# Consistency bar used for the verdict (mirrors the κ bisection band and
# the ~20% relative tolerance the task set for point accuracy).
COVERAGE_LOW = 0.85
COVERAGE_HIGH = 0.95
RMSE_REL_TOL = 0.20

# Ensemble members whose per-member in-sample RMSE / coverage we compare
# against. The blended production forecast is never scored by the
# generator, so the best member is the closest available in-sample
# reference.
_MEMBERS = ("trend_ensemble", "ml_trend", "multi_anchor", "level_anchor")


@dataclass(frozen=True)
class OosFold:
    indicator: str
    geoid: str
    anchor_year: int
    horizon: int
    target_year: int
    actual: float
    point: float
    ci90_low: float
    ci90_high: float
    pop_bucket: str
    method: str
    # True when the instant-mode training set differs from the
    # publication-mode one for this (series, anchor) — i.e. the as-of
    # filter actually removed something.
    as_of_differs: bool

    @property
    def log_err(self) -> float:
        return math.log(self.point / self.actual)

    @property
    def rel_err(self) -> float:
        return (self.point - self.actual) / self.actual

    @property
    def in_ci(self) -> bool:
        return self.ci90_low <= self.actual <= self.ci90_high

    @property
    def hawaii(self) -> bool:
        return self.geoid.startswith("15")


@dataclass(frozen=True)
class Metrics:
    n: int
    mape: float
    rmse_log: float
    rmse_pct: float
    coverage: float
    bias_log: float


def summarise(folds: Sequence[OosFold]) -> Metrics:
    if not folds:
        nan = float("nan")
        return Metrics(0, nan, nan, nan, nan, nan)
    n = len(folds)
    return Metrics(
        n=n,
        mape=sum(abs(f.rel_err) for f in folds) / n,
        rmse_log=math.sqrt(sum(f.log_err ** 2 for f in folds) / n),
        rmse_pct=math.sqrt(sum(f.rel_err ** 2 for f in folds) / n),
        coverage=sum(1 for f in folds if f.in_ci) / n,
        bias_log=sum(f.log_err for f in folds) / n,
    )


def _truth_at(series: Sequence[AcsObservation], year: int) -> AcsObservation | None:
    return next(
        (o for o in series if o.vintage == "1y" and effective_year(o) == year),
        None,
    )


def evaluate_oos(
    series_by_key: dict[tuple[str, str], Sequence[AcsObservation]],
    populations: dict[str, int],
    calibration: dict,
    anchors: Sequence[int],
    max_truth_year: int,
    horizons: Sequence[int] = (1, 2, 3, 4, 5),
    use_ml: bool = True,
    ml_bundle: dict | None = None,
    ml_model_cache: dict | None = None,
    log=None,
) -> tuple[list[OosFold], dict]:
    """Project every (county, indicator) from each anchor through the
    production path and score against 1-year truth.

    Returns (folds, skipped) where `skipped` counts folds dropped for
    want of truth or a projection, keyed by reason.
    """
    from ..acs.ensemble import project_ensemble_multi

    skipped: dict[str, int] = {"no_truth": 0, "no_train": 0, "no_forecast": 0}
    folds: list[OosFold] = []
    ml_series = ml_bundle["series"] if ml_bundle else None
    ml_pops = ml_bundle["populations"] if ml_bundle else None
    ml_panel = ml_bundle["panel"] if ml_bundle else None
    if ml_model_cache is None:
        ml_model_cache = {}

    keys = sorted(series_by_key)
    t0 = time.time()
    for i, (geoid, indicator) in enumerate(keys):
        full_sorted = sorted(series_by_key[(geoid, indicator)],
                             key=lambda o: (effective_year(o), o.vintage))
        pop_bucket = classify_pop(populations.get(geoid)) or WILDCARD
        for anchor in anchors:
            as_of = acs_1y_release_date(anchor)
            train = _truncate(full_sorted, anchor, as_of_date=as_of)
            train_instant = _truncate(full_sorted, anchor, as_of_date=None)
            differs = len(train) != len(train_instant)
            if not train:
                skipped["no_train"] += 1
                continue
            for h in horizons:
                target = anchor + h
                if target > max_truth_year:
                    continue
                truth = _truth_at(full_sorted, target)
                if truth is None or truth.estimate <= 0:
                    skipped["no_truth"] += 1
                    continue
                fp = project_ensemble_multi(
                    train, target, end_year=anchor,
                    calibration=calibration, populations=populations,
                    use_ml=use_ml,
                    ml_series_by_key=ml_series, ml_populations=ml_pops,
                    ml_model_cache=ml_model_cache, ml_panel=ml_panel,
                )
                if fp is None or not math.isfinite(fp.point) or fp.point <= 0:
                    skipped["no_forecast"] += 1
                    continue
                folds.append(OosFold(
                    indicator=indicator, geoid=geoid, anchor_year=anchor,
                    horizon=h, target_year=target, actual=truth.estimate,
                    point=fp.point, ci90_low=fp.ci90_low, ci90_high=fp.ci90_high,
                    pop_bucket=pop_bucket, method=fp.method,
                    as_of_differs=differs,
                ))
        if log is not None and (i + 1) % 100 == 0:
            log(f"      {i + 1}/{len(keys)} series, {len(folds)} folds, "
                f"{time.time() - t0:.0f}s")
    return folds, skipped


# -----------------------------------------------------------------------------
# In-sample references from the calibration payload
# -----------------------------------------------------------------------------
def _in_sample_reference(calibration: dict, indicator: str) -> dict:
    rmse_tab = (calibration.get("rmse_by_indicator_method") or {}).get(indicator, {})
    eval_tab = (calibration.get("evaluation_coverage") or {}).get(indicator, {})
    member_rmse = {m: rmse_tab[m] for m in _MEMBERS if m in rmse_tab}
    best = min(member_rmse.items(), key=lambda kv: kv[1]) if member_rmse else (None, float("nan"))
    return {
        "best_member": best[0],
        "best_member_rmse_pct": best[1],
        "trend_rmse_pct": rmse_tab.get("trend_ensemble", float("nan")),
        "eval_cov_trend": eval_tab.get("trend_ensemble", float("nan")),
        "eval_cov_ml": eval_tab.get("ml_trend", float("nan")),
    }


def _consistent(m: Metrics, ref: dict) -> tuple[bool, list[str]]:
    flags: list[str] = []
    if not (COVERAGE_LOW <= m.coverage <= COVERAGE_HIGH):
        flags.append(f"cov {m.coverage:.1%} outside [{COVERAGE_LOW:.0%},{COVERAGE_HIGH:.0%}]")
    ref_rmse = ref["best_member_rmse_pct"]
    if math.isfinite(ref_rmse) and ref_rmse > 0:
        rel = m.rmse_pct / ref_rmse - 1.0
        if rel > RMSE_REL_TOL:
            flags.append(f"RMSE {rel:+.0%} vs in-sample best member")
    return (not flags), flags


# -----------------------------------------------------------------------------
# Markdown report
# -----------------------------------------------------------------------------
def _pct(x: float) -> str:
    return "—" if not math.isfinite(x) else f"{x:.1%}"


def _f(x: float, nd: int = 3) -> str:
    return "—" if not math.isfinite(x) else f"{x:+.{nd}f}" if nd else f"{x:.0f}"


def _metrics_row(label: str, m: Metrics) -> str:
    return (f"| {label} | {m.n} | {_pct(m.mape)} | {m.rmse_log:.3f} | {_pct(m.rmse_pct)} "
            f"| {_pct(m.coverage)} | {_f(m.bias_log)} |")


_METRICS_HEADER = (
    "| {label} | n | MAPE | RMSE (log) | RMSE (rel) | CI90 cov | mean bias (log) |\n"
    "|---|---:|---:|---:|---:|---:|---:|"
)


def _group(folds: Sequence[OosFold], key) -> dict:
    out: dict = {}
    for f in folds:
        out.setdefault(key(f), []).append(f)
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))


def render_markdown(
    folds: Sequence[OosFold],
    skipped: dict,
    calibration: dict,
    anchors: Sequence[int],
    max_truth_year: int,
    as_of_finding: dict,
    masked: tuple[Sequence[OosFold], dict] | None = None,
    run_date: date | None = None,
) -> str:
    run_date = run_date or date.today()
    lines: list[str] = []
    L = lines.append
    L(f"# ACS out-of-sample check: anchors {', '.join(map(str, anchors))} → truth through {max_truth_year}")
    L("")
    L(f"Run {run_date.isoformat()}. Calibration: schema v{calibration.get('schema_version')}, "
      f"run_date {calibration.get('run_date')}, as_of_mode `{calibration.get('as_of_mode')}`, "
      f"anchors {calibration.get('anchor_years', [])[:1]}…{calibration.get('anchor_years', [])[-1:]}, "
      f"phi_enabled {calibration.get('phi_enabled')}.")
    L("Production path: `project_ensemble_multi` with shipped defaults (ML on, Kalman per allowlist, "
      "conformal-primary intervals), training set truncated to the publication as-of date of the "
      "anchor-year 1-year ACS. Scored on the **blended** forecast — the generator's own "
      "`evaluation_coverage` scores members separately and never the blend.")
    L("")
    L(f"Folds scored: {len(folds)}. Skipped: " +
      ", ".join(f"{k}={v}" for k, v in skipped.items()) + ".")
    L("")

    # Headline per indicator
    L("## Headline: per indicator (all anchors, all horizons)")
    L("")
    L("| Indicator | n | MAPE | RMSE (log) | RMSE (rel) | CI90 cov | bias (log) "
      "| in-sample best member RMSE (rel) | in-sample eval cov (trend / ml) | verdict |")
    L("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
    n_ok = 0
    by_ind = _group(folds, lambda f: f.indicator)
    for ind, items in by_ind.items():
        m = summarise(items)
        ref = _in_sample_reference(calibration, ind)
        ok, flags = _consistent(m, ref)
        n_ok += ok
        L(f"| {ind} | {m.n} | {_pct(m.mape)} | {m.rmse_log:.3f} | {_pct(m.rmse_pct)} | "
          f"{_pct(m.coverage)} | {_f(m.bias_log)} | "
          f"{_pct(ref['best_member_rmse_pct'])} ({ref['best_member']}) | "
          f"{_pct(ref['eval_cov_trend'])} / {_pct(ref['eval_cov_ml'])} | "
          f"{'ok' if ok else '; '.join(flags)} |")
    all_m = summarise(folds)
    L(_metrics_row("**all**", all_m).replace("| **all** |", "| **all** |", 1) + " | | | |")
    L("")
    L(f"{n_ok}/{len(by_ind)} indicators consistent (coverage in [{COVERAGE_LOW:.0%}, "
      f"{COVERAGE_HIGH:.0%}] and relative RMSE within {RMSE_REL_TOL:.0%} of the best "
      f"in-sample member). Pooled coverage {_pct(all_m.coverage)}, pooled MAPE {_pct(all_m.mape)}.")
    L("")

    # By anchor / horizon
    L("## By anchor and horizon")
    L("")
    L(_METRICS_HEADER.format(label="anchor → target (h)"))
    for (a, h), items in _group(folds, lambda f: (f.anchor_year, f.horizon)).items():
        L(_metrics_row(f"{a} → {a + h} (h={h})", summarise(items)))
    L("")
    L("Per indicator, anchor 2023 → 2024 only (the one fold family that never served as an anchor):")
    L("")
    L(_METRICS_HEADER.format(label="Indicator"))
    for ind, items in _group([f for f in folds if f.anchor_year == anchors[-1]],
                             lambda f: f.indicator).items():
        L(_metrics_row(ind, summarise(items)))
    L("")

    # By population bucket
    L("## By population bucket")
    L("")
    L(_METRICS_HEADER.format(label="bucket"))
    for pb, items in _group(folds, lambda f: f.pop_bucket).items():
        L(_metrics_row(pb, summarise(items)))
    L("")
    L("Per indicator × bucket (n / MAPE / CI90 cov):")
    L("")
    buckets = sorted({f.pop_bucket for f in folds})
    L("| Indicator | " + " | ".join(buckets) + " |")
    L("|---|" + "---:|" * len(buckets))
    for ind, items in by_ind.items():
        cells = []
        for pb in buckets:
            m = summarise([f for f in items if f.pop_bucket == pb])
            cells.append(f"{m.n} / {_pct(m.mape)} / {_pct(m.coverage)}" if m.n else "—")
        L(f"| {ind} | " + " | ".join(cells) + " |")
    L("")

    # Hawaii only
    hi = [f for f in folds if f.hawaii]
    L("## Hawaii counties only (15001, 15003, 15007, 15009)")
    L("")
    L(_METRICS_HEADER.format(label="Indicator"))
    for ind, items in _group(hi, lambda f: f.indicator).items():
        L(_metrics_row(ind, summarise(items)))
    L(_metrics_row("**all Hawaii**", summarise(hi)))
    L("")
    L("Hawaii per-indicator cells are 4 counties × 3 folds = 12 folds; one miss moves coverage "
      "by 8pp. Read the pooled row, not the cells.")
    L("")

    # Masked
    if masked is not None:
        m_folds, m_info = masked
        L(f"## Strict check: calibration regenerated with truth masked after {m_info['mask_year']}")
        L("")
        L(f"Calibration re-run in-process on the panel with every observation after "
          f"{m_info['mask_year']} removed (anchors {m_info['anchor_years'][0]}-"
          f"{m_info['anchor_years'][-1]}, publication as-of, ML {'on' if m_info['include_ml'] else 'off'}, "
          f"conformal on, {m_info['elapsed_s']:.0f}s). The same folds are then scored against "
          "it, so no 2023/2024 print can have touched any κ / bias / conformal record.")
        L("")
        L("| Indicator | n | MAPE shipped | MAPE masked | cov shipped | cov masked | bias shipped | bias masked |")
        L("|---|---:|---:|---:|---:|---:|---:|---:|")
        m_by_ind = _group(m_folds, lambda f: f.indicator)
        for ind, items in by_ind.items():
            a = summarise(items)
            b = summarise(m_by_ind.get(ind, []))
            L(f"| {ind} | {a.n} | {_pct(a.mape)} | {_pct(b.mape)} | {_pct(a.coverage)} | "
              f"{_pct(b.coverage)} | {_f(a.bias_log)} | {_f(b.bias_log)} |")
        a = summarise(folds)
        b = summarise(m_folds)
        L(f"| **all** | {a.n} | {_pct(a.mape)} | {_pct(b.mape)} | {_pct(a.coverage)} | "
          f"{_pct(b.coverage)} | {_f(a.bias_log)} | {_f(b.bias_log)} |")
        L("")

    # As-of finding
    L("## As-of mode: what the ablation scripts actually consumed")
    L("")
    L(as_of_finding["text"])
    L("")
    return "\n".join(lines)


# -----------------------------------------------------------------------------
# As-of finding (computed, not asserted)
# -----------------------------------------------------------------------------
def as_of_finding(
    series_by_key: dict[tuple[str, str], Sequence[AcsObservation]],
    folds: Sequence[OosFold],
    anchor_years: Sequence[int],
) -> dict:
    """Quantify whether instant- and publication-mode training sets ever
    differ on this panel, and whether any 5-year observation exists at all."""
    vintages: dict[str, int] = {}
    for obs_list in series_by_key.values():
        for o in obs_list:
            vintages[o.vintage] = vintages.get(o.vintage, 0) + 1
    n_5y = vintages.get("5y", 0)
    n_diff = 0
    n_pairs = 0
    for obs_list in series_by_key.values():
        s = sorted(obs_list, key=lambda o: (effective_year(o), o.vintage))
        for a in anchor_years:
            n_pairs += 1
            if len(_truncate(s, a, as_of_date=acs_1y_release_date(a))) != len(_truncate(s, a)):
                n_diff += 1
    oos_diff = sum(1 for f in folds if f.as_of_differs)
    text = (
        f"The `compare_*_ablation.py` scripts (ML default-on, UI claims, national macro, "
        f"natl_unemp, market, phi, Kalman) call `run_stratified_calibration` without an "
        f"`as_of_mode` argument, so their fold residuals came from an **instant-mode** "
        f"calibration (`effective_year ≤ anchor` only; no publication-date filter). "
        f"Publication mode has existed since 2026-04-27 (`003e5d8`) but only the "
        f"`refresh-data` workflow passes `--as-of-mode publication`.\n\n"
        f"On this panel the distinction is empty. The bundled panel holds "
        f"{vintages.get('1y', 0)} 1-year observations and **{n_5y} 5-year observations** — "
        f"the ML panel index also drops anything that is not `1y` "
        f"(`ml_features.build_panel_index`). For a 1-year print of year Y, "
        f"`effective_year = Y` and `publication_date = acs_1y_release_date(Y)`, which is "
        f"exactly the as-of date publication mode uses for anchor Y, and the filter is "
        f"`<=`. So the two truncations coincide: across {n_pairs} (series, anchor) pairs "
        f"at anchors {anchor_years[0]}-{anchor_years[-1]} the instant and publication "
        f"training sets differ in **{n_diff}** cases, and in {oos_diff} of the "
        f"{len(folds)} OOS folds here.\n\n"
        f"Consequently no 5-year ACS observation whose window overlaps a target year "
        f"could have been in any ablation's information set — there are none in the "
        f"panel to leak. The ablation verdicts stand as publication-equivalent. The "
        f"caveat that does apply to both modes: the auxiliary ML channels (BPS, SAIPE, "
        f"LAUS, market, national macro, UI claims) are keyed by calendar year, not "
        f"release date, so a lag-0 feature at anchor Y assumes the Y value was public by "
        f"the Y 1-year release (September Y+1). Neither as-of mode checks that per "
        f"source; it is a separate question from the 5-year overlap one and is not "
        f"settled here."
    )
    return {"n_5y": n_5y, "n_diff": n_diff, "n_pairs": n_pairs, "oos_diff": oos_diff, "text": text}


# -----------------------------------------------------------------------------
# Masked (strict) calibration
# -----------------------------------------------------------------------------
def masked_calibration(
    series_by_key: dict[tuple[str, str], Sequence[AcsObservation]],
    populations: dict[str, int],
    mask_year: int,
    anchor_start: int,
    include_ml: bool,
    log=None,
) -> tuple[dict, dict]:
    from ..acs.calibration import run_stratified_calibration

    masked_series = {
        k: [o for o in v if effective_year(o) <= mask_year]
        for k, v in series_by_key.items()
    }
    masked_series = {k: v for k, v in masked_series.items() if v}
    anchor_years = list(range(anchor_start, mask_year))  # last anchor has h=1 truth
    t0 = time.time()
    payload = run_stratified_calibration(
        series_by_key=masked_series,
        anchor_years=anchor_years,
        horizons=(1, 2, 3, 4, 5),
        populations=populations,
        as_of_mode="publication",
        include_ml=include_ml,
        include_conformal=True,
    )
    # Strip the diagnostic dumps the writer would strip.
    payload.pop("fold_residuals", None)
    payload.pop("folds_pass1", None)
    info = {
        "mask_year": mask_year,
        "anchor_years": anchor_years,
        "include_ml": include_ml,
        "elapsed_s": time.time() - t0,
    }
    if log is not None:
        log(f"      masked calibration: anchors {anchor_years[0]}-{anchor_years[-1]}, "
            f"{info['elapsed_s']:.0f}s")
    return payload, info


# -----------------------------------------------------------------------------
# CLI
# -----------------------------------------------------------------------------
def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--anchors", type=str, default="2022,2023")
    p.add_argument("--max-truth-year", type=int, default=2024)
    p.add_argument("--horizons", type=str, default="1,2,3,4,5")
    p.add_argument("--calibration", type=Path, default=None,
                   help="Calibration payload to evaluate (default: the shipped one).")
    p.add_argument("--no-ml", action="store_true", default=False)
    p.add_argument("--masked", action="store_true", default=False,
                   help="Also regenerate a calibration with truth masked after "
                        "--mask-year and score the same folds against it.")
    p.add_argument("--mask-year", type=int, default=None,
                   help="Last year of truth the masked calibration may see "
                        "(default: first anchor).")
    p.add_argument("--anchor-start", type=int, default=2014)
    p.add_argument("--out", type=Path, default=None,
                   help="Markdown output (default backtests/results/acs_oos_<lo>_<hi>_<date>.md).")
    p.add_argument("--json", type=Path, default=None, help="Optional fold-level JSON dump.")
    args = p.parse_args(argv)

    def log(msg: str) -> None:
        print(msg, file=sys.stderr)

    from ..acs.anchors import DEFAULT_CALIBRATION_PATH, load_calibration
    from .load_calibration_panel import PanelMissingError, load_panel

    anchors = [int(s) for s in args.anchors.split(",") if s.strip()]
    horizons = [int(s) for s in args.horizons.split(",") if s.strip()]

    log("[1/4] Loading panel + calibration ...")
    try:
        series_by_key, populations, _manifest = load_panel()
    except PanelMissingError as exc:
        log(f"ERROR: {exc}")
        return 2
    calibration = load_calibration(args.calibration or DEFAULT_CALIBRATION_PATH)
    if calibration is None:
        log("ERROR: calibration payload not found")
        return 2

    use_ml = not args.no_ml
    ml_bundle = None
    if use_ml:
        from ..acs.ensemble import _load_bundled_ml_panel
        ml_bundle = _load_bundled_ml_panel()
        if ml_bundle is None:
            log("WARNING: bundled ML panel unavailable; running without ML")
            use_ml = False

    log(f"[2/4] Projecting anchors {anchors} → truth ≤ {args.max_truth_year} "
        f"({len(series_by_key)} series) ...")
    folds, skipped = evaluate_oos(
        series_by_key, populations, calibration, anchors, args.max_truth_year,
        horizons=horizons, use_ml=use_ml, ml_bundle=ml_bundle, log=log,
    )
    log(f"      {len(folds)} folds; skipped {skipped}")

    masked = None
    if args.masked:
        mask_year = args.mask_year or anchors[0]
        log(f"[3/4] Masked calibration (truth ≤ {mask_year}) ...")
        m_cal, m_info = masked_calibration(
            series_by_key, populations, mask_year, args.anchor_start, use_ml, log=log,
        )
        m_folds, _ = evaluate_oos(
            series_by_key, populations, m_cal, anchors, args.max_truth_year,
            horizons=horizons, use_ml=use_ml, ml_bundle=ml_bundle, log=log,
        )
        masked = (m_folds, m_info)
    else:
        log("[3/4] Masked calibration skipped (pass --masked)")

    finding = as_of_finding(series_by_key, folds, list(calibration.get("anchor_years") or anchors))
    report = render_markdown(
        folds, skipped, calibration, anchors, args.max_truth_year, finding, masked=masked,
    )

    out = args.out
    if out is None:
        repo_root = Path(__file__).resolve().parents[4]
        out = (repo_root / "backtests" / "results" /
               f"acs_oos_{anchors[0] + 1}_{args.max_truth_year}_{date.today().isoformat()}.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report)
    log(f"[4/4] Wrote {out}")
    if args.json:
        args.json.write_text(json.dumps([f.__dict__ for f in folds], indent=1))

    # Echo the headline to stdout.
    for ind, items in _group(folds, lambda f: f.indicator).items():
        m = summarise(items)
        print(f"{ind:26s} n={m.n:4d} MAPE={m.mape:6.2%} RMSElog={m.rmse_log:.3f} "
              f"cov={m.coverage:6.1%} bias={m.bias_log:+.3f}")
    m = summarise(folds)
    print(f"{'ALL':26s} n={m.n:4d} MAPE={m.mape:6.2%} RMSElog={m.rmse_log:.3f} "
          f"cov={m.coverage:6.1%} bias={m.bias_log:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
