"""Hidden-data hold-out calibration of anchor weights and SE inflators.

Drives the data-driven elements of the production projection:

1. **Per-(indicator, source) RMSE** — drives inverse-variance anchor
   weights inside `anchors.combined_anchor_rate`.
2. **Per-(indicator, method) RMSE** — drives the macro/trend blend
   weight inside `ensemble._calibrated_macro_weight`.
3. **Per-indicator EMPIRICAL_SE_INFLATOR_OVERRIDE** — derived from
   coverage of the projection's 90% CI on hold-out folds. If coverage
   is below 85% or above 95%, scale the inflator to bring it into
   band. Documented in METHODOLOGY.md.

The calibration is *fully out-of-sample*: for each anchor year T we
re-derive every source rate using only data with publication_year ≤ T,
project forward h years to T+h, and score against the actual ACS 1-year
print at T+h. We then aggregate RMSE across folds.

The output JSON has this shape:

```
{
  "schema_version": 2,
  "run_date": "...",
  "anchor_years": [...],
  "horizon": 2,
  "rmse_by_indicator_source": {
    "B19013_001E": {
      "cpi_honolulu_allitems": 0.045,
      "qcew_hawaii_wages":      0.029,
      ...
    }, ...
  },
  "rmse_by_indicator_method": {
    "B19013_001E": {
      "trend_ensemble": 0.061,
      "multi_anchor":   0.041,
      "ensemble_multi_anchor": 0.038,
    }, ...
  },
  "ci90_coverage_by_indicator_method": { ... },
  "se_inflator_override_by_indicator_method": { ... }
}
```

The `project_acs_2026.py` entry point loads this file (if present) and
threads it through `project_ensemble_multi`.
"""
from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable, Optional, Sequence

from common.models import AcsObservation, ForecastPoint
from .projection import (
    EMPIRICAL_SE_INFLATOR,
    effective_year,
    project_ar1_log_diff,
    project_damped_trend,
)
from .ensemble import combine_forecasts
from .anchors import (
    AnchorRate,
    anchor_as_forecast,
    combined_anchor_rate,
    combined_level_anchor,
    level_anchor_as_forecast,
    METHOD_LEVEL_ANCHOR,
)
from .sources import available_sources


# Coverage band: if 90% CI hits anything outside this, we adjust the
# per-method inflator. 85% and 95% are textbook tolerances around 90%.
COVERAGE_LOWER_BOUND = 0.85
COVERAGE_UPPER_BOUND = 0.95


@dataclass
class HoldOutFold:
    """One hold-out evaluation."""
    indicator: str
    geoid: str
    anchor_year: int
    target_year: int
    horizon: int
    method: str
    actual: float
    projected: float
    ci90_low: float
    ci90_high: float


def _truncate(
    series: Sequence[AcsObservation],
    anchor_year: int,
    as_of_date: Optional[date] = None,
) -> list[AcsObservation]:
    result = [o for o in series if effective_year(o) <= anchor_year]
    if as_of_date is not None:
        result = [o for o in result if o.publication_date <= as_of_date]
    return result


def _project_trend_only(
    train: Sequence[AcsObservation], target_year: int,
    phi: Optional[float] = None,
) -> Optional[ForecastPoint]:
    """Run the trend-only ensemble (damped + ar1, no anchor).

    `phi=None` projects at DEFAULT_PHI. Pass a per-cell phi only when the
    calibration run has `enable_phi=True` — the folds must be built under
    the same phi the consumer will apply, or every kappa/bias record fit
    on them is calibrated for a different model than production runs.
    """
    components: list[ForecastPoint] = []
    if phi is None:
        f_damped = project_damped_trend(train, target_year)
    else:
        f_damped = project_damped_trend(train, target_year, phi=phi)
    if f_damped is not None:
        components.append(f_damped)
    f_ar1 = project_ar1_log_diff(train, target_year)
    if f_ar1 is not None:
        components.append(f_ar1)
    if not components:
        return None
    return combine_forecasts(components, target_year, method_label="trend_ensemble")


def _project_anchor_only(
    train: Sequence[AcsObservation],
    target_year: int,
    anchor_year: int,
    indicator: str,
    per_source_rmse: Optional[dict[str, dict[str, float]]] = None,
) -> Optional[ForecastPoint]:
    """Project from the latest training observation using only the multi-source anchor.

    Uses `train[-1].geoid` so county-level anchors read the right per-county
    series during back-test folds.
    """
    if not train:
        return None
    rate = combined_anchor_rate(
        indicator=indicator,
        end_year=anchor_year,
        calibration=per_source_rmse,
        geoid=train[-1].geoid,
    )
    if rate is None:
        return None
    return anchor_as_forecast(
        latest=train[-1],
        target_year=target_year,
        anchor_rate=rate,
    )


def _per_source_anchor_forecast(
    train: Sequence[AcsObservation],
    target_year: int,
    anchor_year: int,
    indicator: str,
    source_name: str,
) -> Optional[ForecastPoint]:
    """Project at a *single* source's smoothed rate (for per-source RMSE calibration).

    For county-level sources, looks up the source's per-county series via
    `train[-1].geoid`; non-county sources ignore the geoid.
    """
    if not train:
        return None
    sources = [s for s in available_sources(indicator) if s.name == source_name]
    if not sources:
        return None
    src = sources[0]
    rate = src.smoothed_annual_rate(end_year=anchor_year, geoid=train[-1].geoid)
    if rate is None:
        return None
    # Wrap the single rate in an AnchorRate-equivalent for `anchor_as_forecast`.
    single = AnchorRate(
        point_log_rate=rate.log_rate,
        se_log_rate=rate.se_log_rate,
        indicator=indicator,
        end_year=anchor_year,
        components=[(src.name, rate.log_rate, rate.se_log_rate, 1.0)],
    )
    return anchor_as_forecast(
        latest=train[-1],
        target_year=target_year,
        anchor_rate=single,
    )


def _project_level_anchor_only(
    train: Sequence[AcsObservation],
    target_year: int,
    anchor_year: int,
    indicator: str,
) -> Optional[ForecastPoint]:
    """Project using only the level-type anchor for this indicator.

    Used in pass 2 to compute per-method RMSE for `level_anchor`.
    """
    if not train:
        return None
    level_sources = [
        s for s in available_sources(indicator) if s.anchor_type == "level"
    ]
    if not level_sources:
        return None
    level_anchor = combined_level_anchor(
        indicator=indicator,
        end_year=anchor_year,
        geoid=train[-1].geoid,
        sources=level_sources,
    )
    if level_anchor is None:
        return None
    return level_anchor_as_forecast(
        latest=train[-1],
        target_year=target_year,
        level_anchor=level_anchor,
    )


def run_holdout_calibration(
    series_by_key: dict[tuple[str, str], Sequence[AcsObservation]],
    anchor_years: Sequence[int],
    horizon: int = 2,
) -> dict:
    """Run hold-out calibration across all (geoid, indicator) × anchor years.

    Two passes:
    1. Per-source RMSE pass — each source projects alone and we score
       its forecasts against ACS truth. Results feed inverse-variance
       weights for the anchor combiner.
    2. Per-method RMSE pass — using the per-source RMSE from pass 1,
       run the anchor combiner, the trend ensemble, and the joint
       ensemble; score each.

    Both passes use the same anchor years and horizon.
    """
    folds_pass1: list[HoldOutFold] = []
    folds_pass2: list[HoldOutFold] = []

    # Group indicators we'll iterate over.
    indicators_seen: set[str] = set()
    for (_g, ind) in series_by_key:
        indicators_seen.add(ind)

    # ---- Pass 1: per-source RMSE ----
    for (geoid, indicator), full in series_by_key.items():
        full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
        for anchor in anchor_years:
            target_year = anchor + horizon
            actual_obs = next(
                (o for o in full_sorted if effective_year(o) == target_year and o.vintage == "1y"),
                None,
            )
            if actual_obs is None or actual_obs.estimate <= 0:
                continue
            train = _truncate(full_sorted, anchor)
            if not train:
                continue
            for src in available_sources(indicator):
                # Level-type anchors go through the level-anchor path, not
                # the per-source rate-RMSE pass. Including them here would
                # produce explosive log-rate RMSE (e.g. LAUS during COVID)
                # that pollutes rmse_by_indicator_source with useless entries.
                if src.anchor_type == "level":
                    continue
                fp = _per_source_anchor_forecast(
                    train, target_year, anchor, indicator, src.name
                )
                if fp is None:
                    continue
                folds_pass1.append(HoldOutFold(
                    indicator=indicator, geoid=geoid,
                    anchor_year=anchor, target_year=target_year, horizon=horizon,
                    method=f"source:{src.name}",
                    actual=actual_obs.estimate, projected=fp.point,
                    ci90_low=fp.ci90_low, ci90_high=fp.ci90_high,
                ))

    # Aggregate pass 1 to RMSE per (indicator, source).
    rmse_by_indicator_source: dict[str, dict[str, float]] = {}
    for f in folds_pass1:
        if f.actual <= 0:
            continue
        ind = f.indicator
        src = f.method.split(":", 1)[1]
        rmse_by_indicator_source.setdefault(ind, {}).setdefault(src, [])  # type: ignore[arg-type]
        rmse_by_indicator_source[ind][src].append(  # type: ignore[union-attr]
            ((f.projected - f.actual) / f.actual) ** 2
        )
    # Reduce to RMSE.
    for ind in list(rmse_by_indicator_source.keys()):
        for src, sq_errs in list(rmse_by_indicator_source[ind].items()):  # type: ignore[union-attr]
            if not sq_errs:
                del rmse_by_indicator_source[ind][src]
                continue
            rmse_by_indicator_source[ind][src] = math.sqrt(
                sum(sq_errs) / len(sq_errs)
            )

    # ---- Pass 2: per-method RMSE using calibration from pass 1 ----
    rmse_by_indicator_method: dict[str, dict[str, list[float]]] = {}
    coverage_by_indicator_method: dict[str, dict[str, list[int]]] = {}

    for (geoid, indicator), full in series_by_key.items():
        full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
        for anchor in anchor_years:
            target_year = anchor + horizon
            actual_obs = next(
                (o for o in full_sorted if effective_year(o) == target_year and o.vintage == "1y"),
                None,
            )
            if actual_obs is None or actual_obs.estimate <= 0:
                continue
            train = _truncate(full_sorted, anchor)
            if not train:
                continue

            method_runs: dict[str, ForecastPoint] = {}
            tr = _project_trend_only(train, target_year)
            if tr is not None:
                method_runs["trend_ensemble"] = tr
            an = _project_anchor_only(
                train, target_year, anchor, indicator,
                per_source_rmse=rmse_by_indicator_source,
            )
            if an is not None:
                method_runs["multi_anchor"] = an
            lv = _project_level_anchor_only(train, target_year, anchor, indicator)
            if lv is not None:
                method_runs[METHOD_LEVEL_ANCHOR] = lv

            for name, fp in method_runs.items():
                bucket_r = rmse_by_indicator_method.setdefault(indicator, {}).setdefault(name, [])
                bucket_r.append(((fp.point - actual_obs.estimate) / actual_obs.estimate) ** 2)
                bucket_c = coverage_by_indicator_method.setdefault(indicator, {}).setdefault(name, [])
                bucket_c.append(1 if fp.ci90_low <= actual_obs.estimate <= fp.ci90_high else 0)

                folds_pass2.append(HoldOutFold(
                    indicator=indicator, geoid=geoid,
                    anchor_year=anchor, target_year=target_year, horizon=horizon,
                    method=name,
                    actual=actual_obs.estimate, projected=fp.point,
                    ci90_low=fp.ci90_low, ci90_high=fp.ci90_high,
                ))

    rmse_per_method: dict[str, dict[str, float]] = {}
    coverage_per_method: dict[str, dict[str, float]] = {}
    for ind, by_m in rmse_by_indicator_method.items():
        rmse_per_method[ind] = {
            m: math.sqrt(sum(v) / len(v)) if v else math.nan
            for m, v in by_m.items()
        }
    for ind, by_m in coverage_by_indicator_method.items():
        coverage_per_method[ind] = {
            m: sum(v) / len(v) if v else math.nan
            for m, v in by_m.items()
        }

    # SE inflator override per (indicator, method): bring coverage into [85%, 95%]
    # by scaling the implied SE such that the corresponding Gaussian z-quantile
    # would have hit ~90% on this fold population.
    #
    # Closed form for one pass:
    #     observed_z = z(coverage) where coverage = P(|Z| ≤ observed_z)
    #     factor = z(0.90) / observed_z = 1.645 / observed_z
    # For coverage already in band, factor ≈ 1.0 and we leave the global
    # EMPIRICAL_SE_INFLATOR alone.
    #
    # Why this is iterative + conservative
    # ------------------------------------
    # Coverage is a *discrete* fraction (k/n folds). For n=24, the
    # smallest non-zero shift is 1/24 ≈ 4.2%. A single closed-form
    # rescaling may overshoot — e.g. a cell at 95.8% (23/24) gets a
    # narrowing factor ~0.81, which can push it to 83.3% (20/24)
    # instead of landing at 21-22/24. We therefore search across
    # candidate overrides via repeated bisection between an "over-cover"
    # bound (factor that gave coverage ≥ 95%) and an "under-cover"
    # bound (factor that gave coverage < 85%), preferring the
    # over-cover bound on tie — better to be slightly conservative
    # than to under-state uncertainty. If no candidate ever lands
    # inside [85%, 95%], we keep the smallest factor that achieved
    # ≥ 85% coverage (or the original 1.30 if that already qualified).
    se_override: dict[str, dict[str, float]] = {}
    max_iter = 6
    history: dict[tuple[str, str], list[tuple[float, float]]] = {}  # (override, cov)
    coverage_current = {
        ind: {m: c for m, c in by_m.items()}
        for ind, by_m in coverage_per_method.items()
    }
    # Seed history with the un-overridden case.
    for ind, by_m in coverage_current.items():
        for m, cov in by_m.items():
            if not math.isfinite(cov):
                continue
            history.setdefault((ind, m), []).append((EMPIRICAL_SE_INFLATOR, cov))

    for _ in range(max_iter):
        any_changed = False
        next_overrides: dict[tuple[str, str], float] = {}
        for ind, by_m in coverage_current.items():
            for m, cov in by_m.items():
                if not math.isfinite(cov):
                    continue
                if COVERAGE_LOWER_BOUND <= cov <= COVERAGE_UPPER_BOUND:
                    continue
                hist = history[(ind, m)]
                # Two-sided bound: smallest override that gave cov ≥ lower,
                # largest override that gave cov ≤ upper.
                over = [(ov, c) for ov, c in hist if c > COVERAGE_UPPER_BOUND]
                under = [(ov, c) for ov, c in hist if c < COVERAGE_LOWER_BOUND]
                if over and under:
                    # Bisect between the over (largest under-cover override)
                    # and under (smallest over-cover override).
                    smallest_over = min(over, key=lambda x: x[0])[0]
                    largest_under = max(under, key=lambda x: x[0])[0]
                    new_override = round((smallest_over + largest_under) / 2.0, 4)
                else:
                    cov_clipped = max(0.50, min(0.999, cov))
                    observed_z = _normal_inv_cdf(0.5 + cov_clipped / 2.0)
                    target_z = 1.645
                    factor = target_z / max(observed_z, 1e-3)
                    prior = se_override.get(ind, {}).get(m, EMPIRICAL_SE_INFLATOR)
                    new_override = round(prior * factor, 4)
                if abs(new_override - se_override.get(ind, {}).get(m, EMPIRICAL_SE_INFLATOR)) < 1e-3:
                    continue
                next_overrides[(ind, m)] = new_override
                any_changed = True
        if not any_changed:
            break
        for (ind, m), v in next_overrides.items():
            se_override.setdefault(ind, {})[m] = v
        cal_for_verify = {
            "rmse_by_indicator_source": rmse_by_indicator_source,
            "se_inflator_override_by_indicator_method": se_override,
        }
        coverage_current = _verify_post_override_coverage(
            series_by_key, anchor_years, horizon, cal_for_verify,
        )
        for ind, by_m in coverage_current.items():
            for m, cov in by_m.items():
                if not math.isfinite(cov):
                    continue
                key = (ind, m)
                ov = se_override.get(ind, {}).get(m, EMPIRICAL_SE_INFLATOR)
                history.setdefault(key, []).append((ov, cov))

    # Pick the best override per cell: prefer in-band; if multiple in-band,
    # pick the override with coverage closest to 0.90; if none in-band,
    # pick the override with maximum coverage (conservative — over-covers).
    final_override: dict[str, dict[str, float]] = {}
    for (ind, m), hist in history.items():
        in_band = [(ov, c) for ov, c in hist if COVERAGE_LOWER_BOUND <= c <= COVERAGE_UPPER_BOUND]
        if in_band:
            best = min(in_band, key=lambda x: abs(x[1] - 0.90))
        else:
            best = max(hist, key=lambda x: x[1])
        if abs(best[0] - EMPIRICAL_SE_INFLATOR) > 1e-4:
            final_override.setdefault(ind, {})[m] = best[0]
    se_override = final_override

    cal_for_verify = {
        "rmse_by_indicator_source": rmse_by_indicator_source,
        "se_inflator_override_by_indicator_method": se_override,
    }
    coverage_post = _verify_post_override_coverage(
        series_by_key, anchor_years, horizon, cal_for_verify,
    )

    return {
        "schema_version": 2,
        "run_date": date.today().isoformat(),
        "anchor_years": list(anchor_years),
        "horizon": horizon,
        "rmse_by_indicator_source": rmse_by_indicator_source,
        "rmse_by_indicator_method": rmse_per_method,
        "ci90_coverage_by_indicator_method": coverage_per_method,
        "ci90_coverage_post_override": coverage_post,
        "se_inflator_override_by_indicator_method": se_override,
        "folds_pass1": [_fold_to_dict(f) for f in folds_pass1],
        "folds_pass2": [_fold_to_dict(f) for f in folds_pass2],
    }


def _verify_post_override_coverage(
    series_by_key: dict[tuple[str, str], Sequence[AcsObservation]],
    anchor_years: Sequence[int],
    horizon: int,
    calibration: dict,
) -> dict[str, dict[str, float]]:
    """Re-run pass 2 with the calibrated SE overrides to confirm CI bands."""
    # Local import to avoid a circular dependency.
    from .ensemble import _apply_se_override

    coverage: dict[str, dict[str, list[int]]] = {}
    for (geoid, indicator), full in series_by_key.items():
        full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
        for anchor in anchor_years:
            target_year = anchor + horizon
            actual_obs = next(
                (o for o in full_sorted if effective_year(o) == target_year and o.vintage == "1y"),
                None,
            )
            if actual_obs is None or actual_obs.estimate <= 0:
                continue
            train = _truncate(full_sorted, anchor)
            if not train:
                continue

            tr = _project_trend_only(train, target_year)
            if tr is not None:
                tr = _apply_se_override(tr, indicator, "trend_ensemble", calibration)
                coverage.setdefault(indicator, {}).setdefault("trend_ensemble", []).append(
                    1 if tr.ci90_low <= actual_obs.estimate <= tr.ci90_high else 0
                )
            an = _project_anchor_only(
                train, target_year, anchor, indicator,
                per_source_rmse=calibration.get("rmse_by_indicator_source"),
            )
            if an is not None:
                an = _apply_se_override(an, indicator, "multi_anchor", calibration)
                coverage.setdefault(indicator, {}).setdefault("multi_anchor", []).append(
                    1 if an.ci90_low <= actual_obs.estimate <= an.ci90_high else 0
                )

    out: dict[str, dict[str, float]] = {}
    for ind, by_m in coverage.items():
        out[ind] = {m: sum(v) / len(v) if v else math.nan for m, v in by_m.items()}
    return out


def _fold_to_dict(f: HoldOutFold) -> dict:
    return {
        "indicator": f.indicator,
        "geoid": f.geoid,
        "anchor_year": f.anchor_year,
        "target_year": f.target_year,
        "horizon": f.horizon,
        "method": f.method,
        "actual": f.actual,
        "projected": f.projected,
        "ci90_low": f.ci90_low,
        "ci90_high": f.ci90_high,
        "in_ci": int(f.ci90_low <= f.actual <= f.ci90_high),
        "abs_pct_err": abs((f.projected - f.actual) / f.actual) if f.actual > 0 else math.nan,
    }


# -----------------------------------------------------------------------------
# Inverse normal CDF (Beasley-Springer-Moro approximation, sufficient
# precision for coverage→z conversion at this scale).
# -----------------------------------------------------------------------------

def _normal_inv_cdf(p: float) -> float:
    """Approximate inverse standard-normal CDF (Acklam algorithm).

    Used here only to convert observed CI coverage into a "this is what
    z must have been" quantile so we can scale the empirical SE inflator
    to bring 90% coverage into target band. Precision <1e-4 in the
    body of the distribution, which is far more than coverage-band
    targeting requires.
    """
    if not (0.0 < p < 1.0):
        raise ValueError(f"p must be in (0,1), got {p}")
    a = [-3.969683028665376e+01,  2.209460984245205e+02,
         -2.759285104469687e+02,  1.383577518672690e+02,
         -3.066479806614716e+01,  2.506628277459239e+00]
    b = [-5.447609879822406e+01,  1.615858368580409e+02,
         -1.556989798598866e+02,  6.680131188771972e+01,
         -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01,
         -2.400758277161838e+00, -2.549732539343734e+00,
          4.374664141464968e+00,  2.938163982698783e+00]
    d = [ 7.784695709041462e-03,  3.224671290700398e-01,
          2.445134137142996e+00,  3.754408661907416e+00]
    plow = 0.02425
    phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
               ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)
    if p <= phigh:
        q = p - 0.5
        r = q * q
        return (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5]) * q / \
               (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / \
           ((((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1)


def write_calibration(
    payload: dict, path: Path
) -> None:
    """Persist calibration JSON. Drops `folds_*` and `fold_residuals` arrays
    from the on-disk summary file (those go into the back-test report);
    keeps the small RMSE / coverage / override / strata tables that the
    projection loads at runtime.
    """
    DROPPED_KEYS = {"folds_pass1", "folds_pass2", "fold_residuals"}
    summary = {k: v for k, v in payload.items() if k not in DROPPED_KEYS}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)


# =============================================================================
# v3 stratified calibration
# =============================================================================
#
# The v3 layer extends v2 along three axes:
#
# 1. Multi-horizon: holdouts run at h ∈ {1, 2, 3, 4, 5} instead of fixed h=2.
# 2. Population stratification: each county is classified into a 4-level
#    population bucket from its 2020 ACS B01003_001E and the calibration
#    is computed per-cell.
# 3. Geometric bias correction: per-cell `b = mean(log(point/actual))`,
#    clamped to ±10%, gated at n ≥ n_threshold, applied as
#    `point_corrected = point_raw / exp(b)`.
#
# The v3 generator caches per-fold residuals so the SE-override bisection
# can run on cached residuals instead of re-projecting the full panel each
# iteration. This is ~100× faster on the multi-state, multi-horizon panel.

# Default ±10% multiplicative bias clamp, expressed in log space so the
# math composes cleanly with log-space projection. log(1.10) ≈ 0.0953.
DEFAULT_BIAS_CLAMP_LOG: float = math.log(1.10)


@dataclass(frozen=True)
class FoldResidual:
    """One cached projection-vs-truth pairing for a single (geoid, indicator,
    method, anchor, horizon) fold.

    Cached so that the bias-correction and SE-override passes can operate
    in-memory: applying a multiplicative factor to `se_total` (or shifting
    the level by a bias multiplier) and recomputing coverage on the cached
    list is O(folds), versus the v2 approach of re-projecting the entire
    panel inside the override iteration which was O(folds × verify_passes).
    """
    indicator: str
    method: str
    geoid: str
    anchor_year: int
    horizon: int
    pop_bucket: str  # WILDCARD if county population unknown
    h_bucket: str
    actual: float
    point: float
    se_total: float
    se_sample: float
    se_forecast: float
    ci90_low: float
    ci90_high: float


def _apply_bias_to_residual(r: FoldResidual, b: float) -> FoldResidual:
    """Geometric bias correction.

    Multiplies the point and CI bounds by `exp(-b)`; SE components scale
    by the same factor (since SE in log space is invariant under level
    shifts, but in dollar space `σ_y = σ_logy · y`, so dollar-space SE
    rescales proportionally with the level shift).
    """
    if b == 0.0:
        return r
    factor = math.exp(-b)
    return FoldResidual(
        indicator=r.indicator,
        method=r.method,
        geoid=r.geoid,
        anchor_year=r.anchor_year,
        horizon=r.horizon,
        pop_bucket=r.pop_bucket,
        h_bucket=r.h_bucket,
        actual=r.actual,
        point=r.point * factor,
        se_total=r.se_total * factor,
        se_sample=r.se_sample * factor,
        se_forecast=r.se_forecast * factor,
        ci90_low=r.ci90_low * factor,
        ci90_high=r.ci90_high * factor,
    )


def _coverage_at_factor(residuals: Sequence[FoldResidual], factor: float) -> float:
    """Empirical CI90 coverage if SE were rescaled by `factor`.

    The new CI is `point ± factor · (point − ci90_low)`. Since the input
    CI is symmetric (built via `ci_from_se` with `Z=1.645`), this rescales
    the half-width directly without needing to re-evaluate the Gaussian
    quantile. Returns NaN on empty input — caller decides what to do.
    """
    if not residuals:
        return float("nan")
    n_in = 0
    for r in residuals:
        half = factor * (r.point - r.ci90_low)
        new_low = r.point - half
        new_high = r.point + half
        if new_low <= r.actual <= new_high:
            n_in += 1
    return n_in / len(residuals)


def _bisect_se_factor(
    residuals: Sequence[FoldResidual],
    target_low: float = COVERAGE_LOWER_BOUND,
    target_high: float = COVERAGE_UPPER_BOUND,
    max_iter: int = 30,
    tol: float = 5e-3,
) -> tuple[float, float]:
    """Find a multiplicative SE factor that brings coverage into band.

    Wraps `bisect_for_target_coverage` (in `calibration_common.py`) with
    this module's dollar-space `_coverage_at_factor`. The shared
    bisection algorithm is reused by the BLS calibration generator
    against its log-space coverage function.
    """
    from .calibration_common import bisect_for_target_coverage
    return bisect_for_target_coverage(
        coverage_fn=lambda f: _coverage_at_factor(residuals, f),
        target_low=target_low, target_high=target_high,
        max_iter=max_iter, tol=tol,
    )


def _group_residuals(
    residuals: Sequence[FoldResidual],
) -> dict[tuple[str, str, str, str], list[FoldResidual]]:
    """Group residuals into (indicator, method, pop_bucket, h_bucket) cells."""
    groups: dict[tuple[str, str, str, str], list[FoldResidual]] = {}
    for r in residuals:
        key = (r.indicator, r.method, r.pop_bucket, r.h_bucket)
        groups.setdefault(key, []).append(r)
    return groups


def _marginalise_residuals(
    residuals: Sequence[FoldResidual],
    over: str,
) -> dict[tuple, list[FoldResidual]]:
    """Re-group residuals after marginalising out one or more dimensions.

    `over` is one of: "h" (collapse h_bucket), "pop_h" (collapse both).
    The resulting dict has tuple keys `(indicator, method, pop_or_*, h_or_*)`.
    """
    out: dict[tuple, list[FoldResidual]] = {}
    if over == "h":
        for r in residuals:
            key = (r.indicator, r.method, r.pop_bucket, "*")
            out.setdefault(key, []).append(r)
    elif over == "pop_h":
        for r in residuals:
            key = (r.indicator, r.method, "*", "*")
            out.setdefault(key, []).append(r)
    else:
        raise ValueError(f"unknown marginalisation: {over!r}")
    return out


def _estimate_bias_records(
    residuals: Sequence[FoldResidual],
    n_threshold: int,
    bias_clamp_log: float,
    bias_half_life_years: "float | None" = None,
) -> list:
    """Compute geometric bias `b = mean(log(point/actual))` per cell.

    Emits records at three granularities:
    1. Exact (indicator, method, pop, h) cells.
    2. h-marginalised (indicator, method, pop, "*") cells.
    3. Globally marginalised (indicator, method, "*", "*") cells.

    Only cells with n ≥ n_threshold receive a non-zero applied bias; lower-n
    cells are still emitted with `value=0.0` so the round-trip is lossless
    and downstream consumers can see *why* no correction fired.

    The applied bias is clamped to `[-bias_clamp_log, +bias_clamp_log]`;
    `extra["b_raw"]` records the unclamped value and `extra["clamped"]`
    flags whether the clamp fired.

    bias_half_life_years : when set, fold log-ratios are weighted by
        ``0.5 ** ((max_anchor − anchor_year) / half_life)`` — the repo's
        standard recency convention — so regime-specific bias (e.g. the
        2020–22 inflation-surge under-projection) decays out of the
        estimate as newer anchors accumulate instead of being carried
        frozen. ``None`` (default) keeps the unweighted mean. The
        n-threshold gate stays count-based either way.
    """
    from .strata import StrataRecord, WILDCARD

    max_anchor = max((r.anchor_year for r in residuals), default=0)

    def _cell_record(key: tuple, items: Sequence[FoldResidual]) -> StrataRecord:
        ind, method, pop, hb = key
        log_ratios: list[float] = []
        weights: list[float] = []
        for r in items:
            if r.actual <= 0 or r.point <= 0:
                continue
            try:
                lr = math.log(r.point / r.actual)
            except ValueError:
                continue
            if math.isfinite(lr):
                log_ratios.append(lr)
                if bias_half_life_years:
                    weights.append(
                        0.5 ** ((max_anchor - r.anchor_year) / bias_half_life_years)
                    )
                else:
                    weights.append(1.0)
        n = len(log_ratios)
        if n == 0:
            b_raw = 0.0
        else:
            wsum = sum(weights)
            b_raw = (
                sum(w * lr for w, lr in zip(weights, log_ratios)) / wsum
                if wsum > 0 else 0.0
            )
        # Apply n-threshold gate, then clamp.
        if n < n_threshold:
            b_applied = 0.0
            clamped = 0.0
        else:
            b_applied = max(-bias_clamp_log, min(bias_clamp_log, b_raw))
            clamped = 1.0 if abs(b_applied - b_raw) > 1e-12 else 0.0
        return StrataRecord(
            indicator=ind,
            method=method,
            pop_bucket=pop,
            h_bucket=hb,
            value=b_applied,
            n_folds=n,
            extra={"b_raw": b_raw, "clamped": clamped},
        )

    records = []
    for key, items in _group_residuals(residuals).items():
        records.append(_cell_record(key, items))
    for key, items in _marginalise_residuals(residuals, "h").items():
        records.append(_cell_record(key, items))
    for key, items in _marginalise_residuals(residuals, "pop_h").items():
        records.append(_cell_record(key, items))
    return records


def _build_bias_lookup(
    bias_records: Sequence,
    n_threshold: int,
) -> "Callable[[str, str, str, str], float]":
    """Build a fallback lookup over bias records → applied bias `b`.

    Mirrors the strata fallback chain (exact → h-marginalised → global)
    but the floor here is `b=0.0` (no correction).
    """
    from .strata import index_records

    lookup = index_records(
        records=bias_records,
        n_threshold=n_threshold,
        floor_value=0.0,
    )

    def _b(indicator: str, method: str, pop_bucket: str, h_bucket: str) -> float:
        rec, _src = lookup(indicator, method, pop_bucket, h_bucket)
        return rec.value

    return _b


def _bisect_per_cell_records(
    residuals: Sequence[FoldResidual],
    n_threshold: int,
    base_inflator: float,
) -> tuple[list, list, list]:
    """Run per-cell SE bisection, returning (se_records, cov_pre, cov_post).

    `base_inflator` is the global EMPIRICAL_SE_INFLATOR (=1.30). The
    bisection finds a *multiplicative* factor; the absolute κ stored on
    disk is `factor × base_inflator`. Cells with n < n_threshold get
    factor=1.0 (the floor lookup will fall through to a more-aggregated
    cell at projection time).

    Coverage records are emitted at the same granularities as bias
    records — exact, h-marginalised, and globally marginalised — so the
    fallback chain is consistent across all calibrated quantities.
    """
    from .strata import StrataRecord

    def _cells(group_fn) -> dict[tuple, list[FoldResidual]]:
        return group_fn(residuals)

    def _records_for_groups(
        groups: dict[tuple, list[FoldResidual]],
    ) -> tuple[list, list, list]:
        se_recs: list = []
        cov_pre_recs: list = []
        cov_post_recs: list = []
        for key, items in groups.items():
            ind, method, pop, hb = key
            n = len(items)
            cov_pre = _coverage_at_factor(items, 1.0)
            if n < n_threshold or not math.isfinite(cov_pre):
                # Skip bisection; emit factor=1.0 = base_inflator
                factor, cov_post = 1.0, cov_pre
            else:
                factor, cov_post = _bisect_se_factor(items)
            absolute_kappa = factor * base_inflator
            se_recs.append(StrataRecord(
                indicator=ind, method=method, pop_bucket=pop, h_bucket=hb,
                value=absolute_kappa, n_folds=n,
                extra={
                    "factor": factor,
                    "coverage_pre": cov_pre,
                    "coverage_post": cov_post,
                },
            ))
            cov_pre_recs.append(StrataRecord(
                indicator=ind, method=method, pop_bucket=pop, h_bucket=hb,
                value=cov_pre, n_folds=n,
            ))
            cov_post_recs.append(StrataRecord(
                indicator=ind, method=method, pop_bucket=pop, h_bucket=hb,
                value=cov_post, n_folds=n,
            ))
        return se_recs, cov_pre_recs, cov_post_recs

    # Exact cells
    se_x, cov_pre_x, cov_post_x = _records_for_groups(_group_residuals(residuals))
    # h-marginalised
    se_hm, cov_pre_hm, cov_post_hm = _records_for_groups(_marginalise_residuals(residuals, "h"))
    # Globally marginalised
    se_g, cov_pre_g, cov_post_g = _records_for_groups(_marginalise_residuals(residuals, "pop_h"))
    return (
        se_x + se_hm + se_g,
        cov_pre_x + cov_pre_hm + cov_pre_g,
        cov_post_x + cov_post_hm + cov_post_g,
    )


def _rmse_records_from_residuals(
    residuals: Sequence[FoldResidual],
    n_threshold: int,
) -> list:
    """Per-cell RMSE-pct records at all three granularities.

    RMSE is computed on the *bias-corrected* residuals if the caller
    passed those in; otherwise on raw residuals. This routine doesn't
    distinguish — it just operates on whatever residuals it gets.
    """
    from .strata import StrataRecord

    def _rmse(items: Sequence[FoldResidual]) -> float:
        sq = []
        for r in items:
            if r.actual <= 0:
                continue
            sq.append(((r.point - r.actual) / r.actual) ** 2)
        if not sq:
            return float("nan")
        return math.sqrt(sum(sq) / len(sq))

    def _records_for(groups: dict[tuple, list[FoldResidual]]) -> list:
        out: list = []
        for key, items in groups.items():
            ind, method, pop, hb = key
            out.append(StrataRecord(
                indicator=ind, method=method, pop_bucket=pop, h_bucket=hb,
                value=_rmse(items), n_folds=len(items),
            ))
        return out

    return (
        _records_for(_group_residuals(residuals))
        + _records_for(_marginalise_residuals(residuals, "h"))
        + _records_for(_marginalise_residuals(residuals, "pop_h"))
    )


def _marginalised_v2_table(
    records: Sequence,
    *,
    use_h_marg: bool = True,
) -> dict[str, dict[str, float]]:
    """Build a v2-shaped {indicator: {method: float}} table from strata records.

    Picks the globally marginalised ("*", "*") cell when present, falling
    back to the h-marginalised then exact "small" cell as needed. This is
    the table consumed by v2-only consumers that don't know about strata.

    The marginalised cell is computed once by the calibration generator
    (over all populations and horizons in the panel) so this function
    only needs to look it up — no aggregation required.
    """
    from .strata import WILDCARD
    out: dict[str, dict[str, float]] = {}
    for r in records:
        if r.pop_bucket == WILDCARD and r.h_bucket == WILDCARD:
            out.setdefault(r.indicator, {})[r.method] = r.value
    return out


def _marginalised_v2_se_overrides(
    se_records: Sequence,
    base_inflator: float,
    *,
    epsilon: float = 1e-3,
) -> dict[str, dict[str, float]]:
    """Build the v2 sparse override table from v3 SE records.

    Only emits entries where the globally marginalised κ differs from
    `base_inflator` by more than `epsilon` — preserves the v2 semantic
    that overrides are sparse and only listed when meaningful.
    """
    from .strata import WILDCARD
    out: dict[str, dict[str, float]] = {}
    for r in se_records:
        if r.pop_bucket != WILDCARD or r.h_bucket != WILDCARD:
            continue
        if abs(r.value - base_inflator) > epsilon:
            out.setdefault(r.indicator, {})[r.method] = r.value
    return out


# -----------------------------------------------------------------------------
# Public v3 calibration entry point
# -----------------------------------------------------------------------------

def run_stratified_calibration(
    series_by_key: dict[tuple[str, str], Sequence[AcsObservation]],
    anchor_years: Sequence[int],
    horizons: Sequence[int] = (1, 2, 3, 4, 5),
    populations: Optional[dict[str, int]] = None,
    n_threshold: int = 20,
    bias_clamp_log: float = DEFAULT_BIAS_CLAMP_LOG,
    as_of_mode: str = "instant",
    include_ml: bool = False,
    county_data: Optional[dict] = None,
    market_data: Optional[dict] = None,
    national_data: Optional[dict] = None,
    state_data: Optional[dict] = None,
    include_kalman: bool = False,
    include_conformal: bool = False,
    enable_phi: bool = False,
    bias_half_life_years: "float | None" = None,
) -> dict:
    """v3/v4 stratified hold-out calibration.

    Multi-horizon, population-stratified, geometric-bias-corrected. Caches
    per-fold residuals so SE-override bisection and bias estimation operate
    in-memory; runs roughly 100× faster than the v2 verify-pass approach
    on the multi-state, 5-horizon panel.

    When `include_conformal=True` (schema_version 4) the anchor years are
    split into three sets:
      - tuning    (all but last 2): κ bisection + bias estimation
      - calibration (second to last): split-conformal quantile computation
      - evaluation  (last):          honest held-out coverage report

    Parameters
    ----------
    series_by_key : {(geoid, indicator) → [AcsObservation, …]}
        Full historical series; the function truncates internally per
        anchor.
    anchor_years : sequence of integer anchor years.
    horizons : sequence of forecast horizons in years (default 1..5).
    populations : optional {geoid → 2020 population}. Used to classify
        each county into a population bucket. If None, every county
        falls into the wildcard pop bucket (effectively a 1-pop-bucket
        configuration that still benefits from horizon stratification).
    n_threshold : minimum folds required for a cell to receive a
        non-default calibration. Below threshold, the cell's records
        are emitted with floor values (κ = base_inflator, b = 0).
    bias_clamp_log : magnitude of the symmetric clamp on the bias term
        in log space. Default `log(1.10)` ≈ ±10% multiplicative.
    include_conformal : when True, split anchor_years and compute
        per-stratum conformal quantiles (requires ≥ 3 anchor years).
    state_data : optional {series_name → {state_fips → {year → value}}}
        Overrides the bundled STATE_SERIES channel (DOL UI claims) for
        the ML pass. Pass ``{}`` to run an arm with the channel switched
        off — that is how ``compare_ui_claims_ablation`` builds its
        baseline. ``None`` (default) loads the bundled file.
    bias_half_life_years : when set, Pass A weights fold log-ratios by
        recency (half-life in anchor-years) so old-regime bias decays
        out of the correction; None keeps the historical unweighted
        mean. See _estimate_bias_records.
    enable_phi : when True, the per-cell v4 phi values from Pass 0 are
        (a) applied to the Pass 2 trend-fold projections, so kappa/bias
        records are fit under the same phi production will use, and
        (b) marked `phi_enabled: true` in the payload so the ensemble
        consumer applies them. Default False: phi records are still
        emitted as diagnostics, but folds project at DEFAULT_PHI and the
        consumer ignores the records — the configuration the v4 ablation
        validated (see METHODOLOGY.md §v4 phi calibration).

    Returns
    -------
    dict — schema_version=3 (or 4 when include_conformal) payload with
    both strata records and marginalised v2-style tables for backwards
    compatibility.
    """
    from .strata import classify_pop, classify_horizon, WILDCARD, record_to_dict
    from common.publication import acs_1y_release_date as _acs_pub

    if as_of_mode not in ("instant", "publication"):
        raise ValueError(f"as_of_mode must be 'instant' or 'publication', got {as_of_mode!r}")

    def _as_of(anchor: int) -> Optional[date]:
        return _acs_pub(anchor) if as_of_mode == "publication" else None

    populations = populations or {}
    anchor_list = list(anchor_years)
    horizon_list = list(horizons)

    # ---- Fold split for conformal (Phase E) ----
    # When include_conformal and ≥3 anchor years: split into
    #   tuning (all but last 2) → κ + bias
    #   calibration (second-to-last) → conformal quantile
    #   evaluation (last) → honest held-out coverage
    # When not using conformal (or < 3 years): all folds go to tuning.
    if include_conformal and len(anchor_list) >= 3:
        tuning_anchors = anchor_list[:-2]
        calibration_anchors = [anchor_list[-2]]
        evaluation_anchors = [anchor_list[-1]]
    else:
        tuning_anchors = anchor_list
        calibration_anchors = []
        evaluation_anchors = []

    # ---- Pass 0: phi calibration from MOE-derived variance decomposition ----
    # Runs before the fold-residual cache so that, when enable_phi=True,
    # Pass 2 can project trend folds at the calibrated per-cell phi. With
    # enable_phi=False (default) the records are diagnostic only and Pass 2
    # projects at DEFAULT_PHI — matching what the consumer applies.
    # Uses only the series data (no new fetches) — SE is derived from AcsObservation.moe.
    phi_strata_records: list = []
    _phi_pass_run = False
    try:
        from .acs_volatility import decompose_series, cell_phi, PHI_LO, PHI_HI
        from .strata import (
            StrataRecord as _SR, WILDCARD as _WC, classify_pop as _cp,
            PHI_DEFAULT, PHI_N_THRESHOLD, H_BUCKET_BOUNDS,
        )
        # Decompose each (geoid, indicator) series
        decomps_by_key: dict[tuple[str, str], object] = {}
        for (geoid, indicator), full in series_by_key.items():
            full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
            d = decompose_series(full_sorted)
            if d is not None:
                decomps_by_key[(geoid, indicator)] = d

        # Group by (indicator, pop_bucket)
        from collections import defaultdict
        cell_decomps: dict[tuple[str, str], list] = defaultdict(list)
        for (geoid, indicator), d in decomps_by_key.items():
            pb = _cp(populations.get(geoid)) or _WC
            cell_decomps[(indicator, pb)].append(d)

        # Emit StrataRecord for each (indicator, pop_bucket) x h_bucket combo
        # phi doesn't vary by h_bucket (decomp is series-level), but we carry
        # the dimension so lookup shape is uniform with kappa/bias records.
        for (indicator, pop_bucket), decomps in cell_decomps.items():
            phi_val = cell_phi(decomps, n_min=PHI_N_THRESHOLD)
            if phi_val is None:
                continue
            n_obs = sum(d.n_years for d in decomps if d.n_years > 0)
            for h_bucket in H_BUCKET_BOUNDS:
                phi_strata_records.append(_SR(
                    indicator=indicator,
                    method="trend_ensemble",
                    pop_bucket=pop_bucket,
                    h_bucket=h_bucket,
                    value=phi_val,
                    n_folds=n_obs,
                    extra={"phi_lo": PHI_LO, "phi_hi": PHI_HI},
                ))
            # h-marginalised record
            phi_strata_records.append(_SR(
                indicator=indicator,
                method="trend_ensemble",
                pop_bucket=pop_bucket,
                h_bucket=_WC,
                value=phi_val,
                n_folds=n_obs,
                extra={"phi_lo": PHI_LO, "phi_hi": PHI_HI},
            ))

        # Globally marginalised records (indicator, *, *)
        global_decomps: dict[str, list] = defaultdict(list)
        for (indicator, _pb), decomps in cell_decomps.items():
            global_decomps[indicator].extend(decomps)
        for indicator, decomps in global_decomps.items():
            phi_val = cell_phi(decomps, n_min=PHI_N_THRESHOLD)
            if phi_val is None:
                continue
            n_obs = sum(d.n_years for d in decomps if d.n_years > 0)
            phi_strata_records.append(_SR(
                indicator=indicator,
                method="trend_ensemble",
                pop_bucket=_WC,
                h_bucket=_WC,
                value=phi_val,
                n_folds=n_obs,
                extra={"phi_lo": PHI_LO, "phi_hi": PHI_HI},
            ))
        _phi_pass_run = True
    except Exception:
        pass  # phi calibration is optional; fall back to DEFAULT_PHI everywhere

    # Per-cell phi for Pass 2 fold projection — only when enable_phi. The
    # lookup mirrors ensemble._lookup_phi (same records, same threshold) so
    # folds are built under exactly the phi the consumer will apply.
    _phi_for_cell = None
    if enable_phi and phi_strata_records:
        from .strata import index_records as _phi_index_records, PHI_N_THRESHOLD
        from .projection import DEFAULT_PHI as _DEFAULT_PHI

        _phi_lookup_fn = _phi_index_records(
            records=phi_strata_records,
            n_threshold=PHI_N_THRESHOLD,
            floor_value=_DEFAULT_PHI,
        )

        def _phi_for_cell(indicator: str, pop_bucket: str, h_bucket: str) -> float:
            rec, _src = _phi_lookup_fn(indicator, "trend_ensemble", pop_bucket, h_bucket)
            return rec.value

    # Pass 1 uses all anchors (per-source RMSE is not split).
    # ---- Pass 1: per-source RMSE (h-marginalised; reused from v2 path) ----
    folds_pass1: list[HoldOutFold] = []
    for (geoid, indicator), full in series_by_key.items():
        full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
        for anchor in anchor_list:
            for h in horizon_list:
                target_year = anchor + h
                actual_obs = next(
                    (o for o in full_sorted
                     if effective_year(o) == target_year and o.vintage == "1y"),
                    None,
                )
                if actual_obs is None or actual_obs.estimate <= 0:
                    continue
                train = _truncate(full_sorted, anchor, as_of_date=_as_of(anchor))
                if not train:
                    continue
                for src in available_sources(indicator):
                    if src.anchor_type == "level":
                        continue  # level sources use level_anchor path, not rate RMSE
                    fp = _per_source_anchor_forecast(
                        train, target_year, anchor, indicator, src.name
                    )
                    if fp is None:
                        continue
                    folds_pass1.append(HoldOutFold(
                        indicator=indicator, geoid=geoid,
                        anchor_year=anchor, target_year=target_year, horizon=h,
                        method=f"source:{src.name}",
                        actual=actual_obs.estimate, projected=fp.point,
                        ci90_low=fp.ci90_low, ci90_high=fp.ci90_high,
                    ))
    rmse_by_indicator_source: dict[str, dict] = {}
    for f in folds_pass1:
        if f.actual <= 0:
            continue
        ind = f.indicator
        src = f.method.split(":", 1)[1]
        rmse_by_indicator_source.setdefault(ind, {}).setdefault(src, [])  # type: ignore[arg-type]
        rmse_by_indicator_source[ind][src].append(  # type: ignore[union-attr]
            ((f.projected - f.actual) / f.actual) ** 2
        )
    for ind in list(rmse_by_indicator_source.keys()):
        for src, sq_errs in list(rmse_by_indicator_source[ind].items()):  # type: ignore[union-attr]
            if not sq_errs:
                del rmse_by_indicator_source[ind][src]
                continue
            rmse_by_indicator_source[ind][src] = math.sqrt(sum(sq_errs) / len(sq_errs))

    # ---- Pass 2: produce FoldResidual cache for all methods ----
    # Collect residuals for every anchor year; split into tuning /
    # calibration / evaluation after the loop.
    all_fold_residuals: list[FoldResidual] = []
    for (geoid, indicator), full in series_by_key.items():
        full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
        pop_bucket = classify_pop(populations.get(geoid)) or WILDCARD
        for anchor in anchor_list:
            for h in horizon_list:
                target_year = anchor + h
                actual_obs = next(
                    (o for o in full_sorted
                     if effective_year(o) == target_year and o.vintage == "1y"),
                    None,
                )
                if actual_obs is None or actual_obs.estimate <= 0:
                    continue
                train = _truncate(full_sorted, anchor, as_of_date=_as_of(anchor))
                if not train:
                    continue
                h_bucket = classify_horizon(h) or WILDCARD

                tr = _project_trend_only(
                    train, target_year,
                    phi=(
                        _phi_for_cell(indicator, pop_bucket, h_bucket)
                        if _phi_for_cell is not None else None
                    ),
                )
                if tr is not None:
                    all_fold_residuals.append(FoldResidual(
                        indicator=indicator, method="trend_ensemble",
                        geoid=geoid, anchor_year=anchor, horizon=h,
                        pop_bucket=pop_bucket, h_bucket=h_bucket,
                        actual=actual_obs.estimate,
                        point=tr.point, se_total=tr.se_total,
                        se_sample=tr.se_sample, se_forecast=tr.se_forecast,
                        ci90_low=tr.ci90_low, ci90_high=tr.ci90_high,
                    ))
                an = _project_anchor_only(
                    train, target_year, anchor, indicator,
                    per_source_rmse=rmse_by_indicator_source,
                )
                if an is not None:
                    all_fold_residuals.append(FoldResidual(
                        indicator=indicator, method="multi_anchor",
                        geoid=geoid, anchor_year=anchor, horizon=h,
                        pop_bucket=pop_bucket, h_bucket=h_bucket,
                        actual=actual_obs.estimate,
                        point=an.point, se_total=an.se_total,
                        se_sample=an.se_sample, se_forecast=an.se_forecast,
                        ci90_low=an.ci90_low, ci90_high=an.ci90_high,
                    ))

    # ---- Pass 2b (optional): ml_trend FoldResidual cache ----
    # Trains one HGB model per (indicator, cutoff=anchor) — ~16 indicators
    # × ~9 anchor years = 144 fits @ ~2s each ≈ 5min. Reuses each model
    # across all (geoid, h) folds at the same cutoff so prediction is
    # cheap (~150 predict() calls per cutoff). Restructured loop ordering
    # is intentional — per-anchor model fitting must happen ONCE per
    # anchor, not once per (geoid, h) combination.
    if include_ml:
        from .ml_features import (
            build_panel_index as _build_panel_index,
            load_county_data as _load_cty,
            load_market_signals_data as _load_mkt,
            load_national_macro_data as _load_nm,
            load_state_data as _load_st,
        )
        from .ml_trend import (
            train_ml_model as _train_ml_model,
            project_ml_trend as _project_ml_trend,
            METHOD_NAME as _ML_METHOD,
        )

        _cty = county_data if county_data is not None else _load_cty()
        _mkt = market_data if market_data is not None else _load_mkt()
        _nm = national_data if national_data is not None else _load_nm()
        _st = state_data if state_data is not None else _load_st()
        ml_panel = _build_panel_index(
            series_by_key,
            county_data=_cty,
            market_data=_mkt,
            national_data=_nm,
            state_data=_st,
        )
        # Group (geoid, indicator) by indicator so we can iterate
        # indicator-major, anchor-major, then geoid for each.
        by_indicator: dict[str, list[tuple[str, list[AcsObservation]]]] = {}
        for (geoid, indicator), full in series_by_key.items():
            full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
            by_indicator.setdefault(indicator, []).append((geoid, full_sorted))

        for indicator, county_list in by_indicator.items():
            for anchor in anchor_list:
                model = _train_ml_model(
                    series_by_key=series_by_key,
                    populations=populations,
                    indicator=indicator,
                    cutoff_year=anchor,
                    horizons=horizon_list,
                    panel=ml_panel,
                )
                if model is None:
                    continue
                for geoid, full_sorted in county_list:
                    pop_bucket = classify_pop(populations.get(geoid)) or WILDCARD
                    train = _truncate(full_sorted, anchor, as_of_date=_as_of(anchor))
                    if not train:
                        continue
                    for h in horizon_list:
                        target_year = anchor + h
                        actual_obs = next(
                            (o for o in full_sorted
                             if effective_year(o) == target_year and o.vintage == "1y"),
                            None,
                        )
                        if actual_obs is None or actual_obs.estimate <= 0:
                            continue
                        h_bucket = classify_horizon(h) or WILDCARD
                        ml_fp = _project_ml_trend(
                            series_observations=train,
                            target_year=target_year,
                            model=model,
                            panel=ml_panel,
                            populations=populations,
                        )
                        if ml_fp is None:
                            continue
                        all_fold_residuals.append(FoldResidual(
                            indicator=indicator, method=_ML_METHOD,
                            geoid=geoid, anchor_year=anchor, horizon=h,
                            pop_bucket=pop_bucket, h_bucket=h_bucket,
                            actual=actual_obs.estimate,
                            point=ml_fp.point, se_total=ml_fp.se_total,
                            se_sample=ml_fp.se_sample, se_forecast=ml_fp.se_forecast,
                            ci90_low=ml_fp.ci90_low, ci90_high=ml_fp.ci90_high,
                        ))

    # ---- Pass 2c (optional): Kalman state-space FoldResidual cache ----
    # Runs the Kalman filter per (geoid, indicator, anchor, h). Substantially
    # faster than ML (no model training — forward filter is O(n_years) per fold).
    if include_kalman:
        from ..kalman.project import project_kalman as _project_kalman, METHOD_NAME as _KAL_METHOD
        for (geoid, indicator), full in series_by_key.items():
            full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
            pop_bucket = classify_pop(populations.get(geoid)) or WILDCARD
            for anchor in anchor_list:
                train = _truncate(full_sorted, anchor, as_of_date=_as_of(anchor))
                if not train:
                    continue
                for h in horizon_list:
                    target_year = anchor + h
                    actual_obs = next(
                        (o for o in full_sorted
                         if effective_year(o) == target_year and o.vintage == "1y"),
                        None,
                    )
                    if actual_obs is None or actual_obs.estimate <= 0:
                        continue
                    h_bucket = classify_horizon(h) or WILDCARD
                    kal_fp = _project_kalman(
                        series_observations=train,
                        target_year=target_year,
                        end_year=anchor,
                        calibration={"rmse_by_indicator_source": rmse_by_indicator_source},
                        geoid=geoid,
                    )
                    if kal_fp is None:
                        continue
                    all_fold_residuals.append(FoldResidual(
                        indicator=indicator, method=_KAL_METHOD,
                        geoid=geoid, anchor_year=anchor, horizon=h,
                        pop_bucket=pop_bucket, h_bucket=h_bucket,
                        actual=actual_obs.estimate,
                        point=kal_fp.point, se_total=kal_fp.se_total,
                        se_sample=kal_fp.se_sample, se_forecast=kal_fp.se_forecast,
                        ci90_low=kal_fp.ci90_low, ci90_high=kal_fp.ci90_high,
                    ))

    # ---- Pass 2d: level_anchor FoldResidual cache ----
    # Evaluates level-type anchor sources (LAUS, SAIPE) which predict the ACS
    # value directly as a level rather than as a growth rate. Always included
    # (not gated on include_ml or include_kalman) since the data files are
    # bundled and the computation is cheap (one latest_level() call per fold).
    for (geoid, indicator), full in series_by_key.items():
        full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
        pop_bucket = classify_pop(populations.get(geoid)) or WILDCARD
        # Quick check: does this indicator have any level sources?
        _level_sources_for_ind = [
            s for s in available_sources(indicator) if s.anchor_type == "level"
        ]
        if not _level_sources_for_ind:
            continue
        for anchor in anchor_list:
            train = _truncate(full_sorted, anchor, as_of_date=_as_of(anchor))
            if not train:
                continue
            for h in horizon_list:
                target_year = anchor + h
                actual_obs = next(
                    (o for o in full_sorted
                     if effective_year(o) == target_year and o.vintage == "1y"),
                    None,
                )
                if actual_obs is None or actual_obs.estimate <= 0:
                    continue
                h_bucket = classify_horizon(h) or WILDCARD
                lv_fp = _project_level_anchor_only(
                    train, target_year, anchor, indicator,
                )
                if lv_fp is None:
                    continue
                all_fold_residuals.append(FoldResidual(
                    indicator=indicator, method=METHOD_LEVEL_ANCHOR,
                    geoid=geoid, anchor_year=anchor, horizon=h,
                    pop_bucket=pop_bucket, h_bucket=h_bucket,
                    actual=actual_obs.estimate,
                    point=lv_fp.point, se_total=lv_fp.se_total,
                    se_sample=lv_fp.se_sample, se_forecast=lv_fp.se_forecast,
                    ci90_low=lv_fp.ci90_low, ci90_high=lv_fp.ci90_high,
                ))

    # ---- Pass 2e: mean_reversion FoldResidual cache (diagnostic only) ----
    # AR(1)-toward-mean model for unemployment (S2301) built on the LAUS
    # county series with a county ACS↔LAUS offset. Evaluated 2026-08-21:
    # NULL — loses to trend/ml by 20+ RMSE points at every horizon (see
    # acs/mean_reversion.py docstring and METHODOLOGY §S2301 null). Kept
    # in the fold cache so the null is reproducible; the ensemble does
    # NOT consume this method.
    from .mean_reversion import (
        project_mean_reversion as _project_mr,
        METHOD_NAME as _MR_METHOD,
        SUPPORTED_INDICATORS as _MR_INDICATORS,
    )
    for (geoid, indicator), full in series_by_key.items():
        if indicator not in _MR_INDICATORS:
            continue
        full_sorted = sorted(full, key=lambda o: (effective_year(o), o.vintage))
        pop_bucket = classify_pop(populations.get(geoid)) or WILDCARD
        for anchor in anchor_list:
            train = _truncate(full_sorted, anchor, as_of_date=_as_of(anchor))
            if not train:
                continue
            for h in horizon_list:
                target_year = anchor + h
                actual_obs = next(
                    (o for o in full_sorted
                     if effective_year(o) == target_year and o.vintage == "1y"),
                    None,
                )
                if actual_obs is None or actual_obs.estimate <= 0:
                    continue
                h_bucket = classify_horizon(h) or WILDCARD
                mr_fp = _project_mr(train, target_year, end_year=anchor)
                if mr_fp is None:
                    continue
                all_fold_residuals.append(FoldResidual(
                    indicator=indicator, method=_MR_METHOD,
                    geoid=geoid, anchor_year=anchor, horizon=h,
                    pop_bucket=pop_bucket, h_bucket=h_bucket,
                    actual=actual_obs.estimate,
                    point=mr_fp.point, se_total=mr_fp.se_total,
                    se_sample=mr_fp.se_sample, se_forecast=mr_fp.se_forecast,
                    ci90_low=mr_fp.ci90_low, ci90_high=mr_fp.ci90_high,
                ))

    # ---- Fold split: tuning / calibration / evaluation ----
    tuning_set = set(tuning_anchors)
    calibration_set = set(calibration_anchors)
    evaluation_set = set(evaluation_anchors)

    fold_residuals = [r for r in all_fold_residuals if r.anchor_year in tuning_set or not tuning_anchors]
    conformal_residuals = [r for r in all_fold_residuals if r.anchor_year in calibration_set]
    evaluation_residuals = [r for r in all_fold_residuals if r.anchor_year in evaluation_set]
    # When conformal is off, tuning_anchors == anchor_list so fold_residuals == all.
    if not calibration_anchors:
        fold_residuals = all_fold_residuals

    # ---- Pass A: bias estimation per cell (with marginalisation) ----
    bias_records = _estimate_bias_records(
        fold_residuals, n_threshold, bias_clamp_log,
        bias_half_life_years=bias_half_life_years,
    )

    # ---- Pass A.5: apply bias to fold residuals in memory ----
    bias_lookup = _build_bias_lookup(bias_records, n_threshold)
    bias_corrected: list[FoldResidual] = []
    for r in fold_residuals:
        b = bias_lookup(r.indicator, r.method, r.pop_bucket, r.h_bucket)
        bias_corrected.append(_apply_bias_to_residual(r, b))

    # ---- Pass B: per-cell SE override on bias-corrected residuals ----
    se_records, cov_pre_records, cov_post_records = _bisect_per_cell_records(
        bias_corrected, n_threshold, base_inflator=EMPIRICAL_SE_INFLATOR,
    )

    # ---- Pass C: per-cell RMSE on bias-corrected residuals ----
    rmse_records = _rmse_records_from_residuals(bias_corrected, n_threshold)

    # ---- Pass D (optional): split-conformal quantiles from calibration folds ----
    conformal_records: list = []
    evaluation_coverage: dict = {}
    if include_conformal and conformal_residuals:
        from .conformal import compute_conformal_quantiles, build_conformal_lookup
        # Apply bias correction to calibration residuals using tuning-fold bias.
        conformal_bc: list[FoldResidual] = []
        for r in conformal_residuals:
            b = bias_lookup(r.indicator, r.method, r.pop_bucket, r.h_bucket)
            conformal_bc.append(_apply_bias_to_residual(r, b))
        conformal_records = compute_conformal_quantiles(
            conformal_bc, target_coverage=0.90, n_threshold=n_threshold,
        )
        # Honest evaluation coverage on evaluation folds, reproducing the
        # production interval exactly (ensemble.py order: bias → κ →
        # conformal). Two earlier defects fixed here: (1) "kappa_half" was
        # the *raw* half-width — the κ override was never applied, so the
        # reported evaluation didn't measure the production interval;
        # (2) the conformal half used the non-bias-corrected se_total,
        # while both the quantile computation above and the production
        # consumer standardise by the bias-corrected SE.
        if evaluation_residuals:
            from .strata import index_records as _idx_records
            conf_lookup = build_conformal_lookup(conformal_records)
            _kappa_lookup = _idx_records(
                records=se_records,
                n_threshold=n_threshold,
                floor_value=EMPIRICAL_SE_INFLATOR,
            )
            eval_covered: dict[tuple[str, str], list[bool]] = {}
            for r in evaluation_residuals:
                b = bias_lookup(r.indicator, r.method, r.pop_bucket, r.h_bucket)
                r_bc = _apply_bias_to_residual(r, b)
                # κ override, mirroring ensemble._apply_se_override.
                k_rec, _src = _kappa_lookup(
                    r.indicator, r.method, r.pop_bucket, r.h_bucket
                )
                kappa = k_rec.value
                se_post_kappa = max(
                    r_bc.se_total * (kappa / EMPIRICAL_SE_INFLATOR),
                    r_bc.se_sample,
                )
                kappa_half = 1.645 * se_post_kappa
                # Conformal half-width: q · se_total (bias-corrected, pre-κ),
                # mirroring ensemble._apply_conformal_interval's se_pre_kappa.
                # Conformal-primary policy (2026-08-21): where a stratum
                # record exists the conformal interval replaces the κ CI;
                # κ stands only for uncovered strata.
                q = conf_lookup(r.indicator, r.method, r.pop_bucket, r.h_bucket)
                half = (q * r_bc.se_total) if q is not None else kappa_half
                in_ci = abs(r_bc.actual - r_bc.point) <= half
                key = (r.indicator, r.method)
                eval_covered.setdefault(key, []).append(in_ci)
            evaluation_coverage = {
                ind: {meth: sum(v) / len(v) for (i, m), v in eval_covered.items()
                      if i == ind for meth in [m]}
                for ind in {i for i, _ in eval_covered}
            }

    # ---- Build marginalised v2-style tables for back-compat ----
    rmse_v2 = _marginalised_v2_table(rmse_records)
    cov_pre_v2 = _marginalised_v2_table(cov_pre_records)
    cov_post_v2 = _marginalised_v2_table(cov_post_records)
    se_override_v2 = _marginalised_v2_se_overrides(se_records, EMPIRICAL_SE_INFLATOR)

    # v3 = no phi, no conformal
    # v4 = phi calibration only
    # v5 = phi + conformal
    if include_conformal:
        schema_v = 5
    elif _phi_pass_run and phi_strata_records:
        schema_v = 4
    else:
        schema_v = 3

    return {
        "schema_version": schema_v,
        "run_date": date.today().isoformat(),
        "anchor_years": anchor_list,
        "horizons": horizon_list,
        "as_of_mode": as_of_mode,
        # Consumer gate for the v4 per-cell phi records: only when True did
        # the Pass 2 folds project at per-cell phi, so only then may the
        # ensemble apply it (ensemble._lookup_phi). Records are always
        # emitted below for diagnostics.
        "phi_enabled": bool(enable_phi and _phi_pass_run),
        # Pass 1 (h-marginalised): per-source RMSE
        "rmse_by_indicator_source": rmse_by_indicator_source,
        # v3 strata records
        "strata_records": {
            "rmse": [record_to_dict(r) for r in rmse_records],
            "coverage_pre": [record_to_dict(r) for r in cov_pre_records],
            "coverage_post": [record_to_dict(r) for r in cov_post_records],
            "se_inflator": [record_to_dict(r) for r in se_records],
            "bias": [record_to_dict(r) for r in bias_records],
            "phi": [record_to_dict(r) for r in phi_strata_records],
        },
        # Phase E: split-conformal quantiles (schema_version 4 only)
        "conformal_quantile_by_stratum": [
            {
                "indicator": r.indicator, "method": r.method,
                "pop_bucket": r.pop_bucket, "h_bucket": r.h_bucket,
                "quantile": r.quantile,
                "n_calibration": r.n_calibration,
                "target_coverage": r.target_coverage,
            }
            for r in conformal_records
        ],
        "evaluation_coverage": evaluation_coverage,
        # Marginalised v2-style tables for back-compat
        "rmse_by_indicator_method": rmse_v2,
        "ci90_coverage_by_indicator_method": cov_pre_v2,
        "ci90_coverage_post_override": cov_post_v2,
        "se_inflator_override_by_indicator_method": se_override_v2,
        # Diagnostic dump (stripped at write time by `write_calibration`)
        "fold_residuals": [
            {
                "indicator": r.indicator, "method": r.method, "geoid": r.geoid,
                "anchor_year": r.anchor_year, "horizon": r.horizon,
                "pop_bucket": r.pop_bucket, "h_bucket": r.h_bucket,
                "actual": r.actual, "point": r.point,
                "se_total": r.se_total, "ci90_low": r.ci90_low, "ci90_high": r.ci90_high,
                "in_ci": int(r.ci90_low <= r.actual <= r.ci90_high),
            }
            for r in all_fold_residuals
        ],
        "folds_pass1": [_fold_to_dict(f) for f in folds_pass1],
    }
