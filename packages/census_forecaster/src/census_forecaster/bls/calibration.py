"""Stratified BLS CPI calibration (v3).

Mirrors `census_forecaster.acs.calibration.run_stratified_calibration` but
operates in **log space** on monthly CPI projections instead of dollar
space on annual ACS estimates. Three differences from the ACS path:

1. **Log-symmetric CIs.** A BLS projection's CI is built as
   `point × exp(±Z·SE_log)` (the projection is log-space). Rescaling SE
   by a factor `f` produces new bounds `point × exp(±Z·f·SE_log)` —
   multiplicatively, not additively. The bisection works on the same
   coverage residuals but uses the log-space rescale.

2. **Three-axis stratification.** Cells are keyed by
   `(series_id, h_bucket, vol_regime)`:
     * `series_id` — the BLS series (e.g. `CUURS49ASA0`)
     * `h_bucket` — `short` for h ∈ {2, 4} months, `long` for h ≥ 6 months
     * `vol_regime` — `low` if the rolling 24-month return SD ≤ per-series
       median; `high` otherwise
   The volatility regime axis captures the 2021-2023 inflation spike vs
   the 2010s calm period — a single κ over both regimes either over-pads
   the calm or under-pads the spike.

3. **Monthly cadence.** h is in months, not years. h_bucket boundaries
   reflect the bimonthly Honolulu cadence (2/4 vs 6+).

Output schema mirrors the ACS v3 calibration: top-level marginalised
tables for back-compat, plus `strata_records` carrying the per-cell
bias and κ values with `n_folds` for the consumer's fallback chain.

When applied at projection time, the consumer threads
`(series_id, h_bucket, vol_regime)` into a strata lookup and applies:
  - bias correction first: `ratio_corrected = ratio_raw / exp(b)`
  - SE inflator second: `se_log_calibrated = κ · se_log_raw`
And recomputes the CI bounds in log space.
"""
from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from ..acs.strata import (
    DEFAULT_N_THRESHOLD,
    WILDCARD,
    StrataRecord,
    index_records,
    record_to_dict,
)
from .projection import (
    _Z_90,
    PROJ_DAMPING,
    PROJ_MONTHLY_CAP,
    project_forward_full,
)

# ---------------------------------------------------------------------------
# Stratification primitives
# ---------------------------------------------------------------------------

# h-bucket boundaries (months). short captures the dominant bimonthly
# cadence (2- and 4-month horizons); long captures everything else
# (6 months and beyond, where damping accumulates and SE compounds faster).
H_BUCKETS_BLS: dict[str, tuple[int, ...]] = {
    "short": (1, 2, 3, 4, 5),
    "long":  (6, 7, 8, 9, 10, 11, 12),
}

# Volatility regime: rolling 24-month return SD threshold. Each series
# carries its own median across all observation windows (computed by the
# calibration generator) so the same threshold isn't imposed across
# rent (low-volatility) and gasoline (high-volatility).
VOL_REGIMES: tuple[str, str] = ("low", "high")

# Default ±10% multiplicative bias clamp in log space. log(1.10) ≈ 0.0953.
DEFAULT_BIAS_CLAMP_LOG: float = math.log(1.10)

# Coverage band for the per-cell SE bisection.
COVERAGE_LOWER_BOUND: float = 0.85
COVERAGE_UPPER_BOUND: float = 0.95


def h_bucket_for_months(h_months: int) -> str:
    """Map an integer horizon (months) to a BLS h-bucket."""
    if h_months is None or h_months <= 0:
        return WILDCARD
    for name, hs in H_BUCKETS_BLS.items():
        if h_months in hs:
            return name
    # Beyond defined buckets → clamp to longest
    return "long"


def vol_regime_for_value(
    vol_value: float | None,
    threshold: float | None,
) -> str:
    """Classify a per-anchor volatility value as low/high vs threshold.

    Returns the WILDCARD sentinel when either input is missing or
    non-finite — the fallback chain treats it as "skip exact, marginalise
    over vol_regime".
    """
    if vol_value is None or threshold is None:
        return WILDCARD
    if not (math.isfinite(vol_value) and math.isfinite(threshold)):
        return WILDCARD
    return "low" if vol_value <= threshold else "high"


# ---------------------------------------------------------------------------
# Fold residual cache
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class BlsFoldResidual:
    """One cached BLS projection-vs-truth fold.

    Cached so the bias-correction and SE-override passes can operate
    in-memory: applying a multiplicative factor to `forecast_se_log`
    (or shifting the level by a bias multiplier) and recomputing
    coverage on the cached list is O(folds). The bisection runs in
    milliseconds per cell instead of re-projecting the full panel.

    All numerics are stored in log space (the projection's native
    domain) plus dollar-space CI bounds for direct coverage checks.

    Fields:
        series_id    BLS series identifier (e.g. ``CUURS49ASA0``)
        anchor_year  / anchor_month — last observed point at fold time
        target_year  / target_month — held-out actual
        h_months     — anchor → target distance in months
        h_bucket     — short / long
        vol_regime   — low / high (computed against per-series median)
        actual       — actual published CPI at target
        point        — uncalibrated projected CPI (κ=1.0)
        residual_log_std  — projection's in-sample residual SD
        forecast_se_log   — projection's closed-form SE in log space (κ=1.0)
        ci90_low / ci90_high — log-space CI bounds at κ=1.0
    """
    series_id: str
    anchor_year: int
    anchor_month: int
    target_year: int
    target_month: int
    h_months: int
    h_bucket: str
    vol_regime: str
    actual: float
    point: float
    residual_log_std: float
    forecast_se_log: float
    ci90_low: float
    ci90_high: float


# ---------------------------------------------------------------------------
# In-memory helpers (log space)
# ---------------------------------------------------------------------------

def _apply_bias_to_residual(r: BlsFoldResidual, b: float) -> BlsFoldResidual:
    """Geometric bias correction in log space.

    Multiplies the point and CI bounds by `exp(-b)`. The forecast SE in
    log space is invariant under a level shift (a bias correction is
    multiplicative on the level but doesn't change residual variance).
    """
    if b == 0.0 or not math.isfinite(b):
        return r
    factor = math.exp(-b)
    return BlsFoldResidual(
        series_id=r.series_id,
        anchor_year=r.anchor_year,
        anchor_month=r.anchor_month,
        target_year=r.target_year,
        target_month=r.target_month,
        h_months=r.h_months,
        h_bucket=r.h_bucket,
        vol_regime=r.vol_regime,
        actual=r.actual,
        point=r.point * factor,
        residual_log_std=r.residual_log_std,
        forecast_se_log=r.forecast_se_log,
        ci90_low=r.ci90_low * factor,
        ci90_high=r.ci90_high * factor,
    )


def _coverage_at_factor(
    residuals: Sequence[BlsFoldResidual],
    factor: float,
) -> float:
    """Empirical coverage if SE were rescaled by `factor` in log space.

    The new CI is `point × exp(±Z·factor·se_log)`. This is the log-space
    counterpart of ACS's dollar-space rescale; the multiplicative form
    preserves the projection's natural geometry.
    """
    if not residuals:
        return float("nan")
    n_in = 0
    for r in residuals:
        if r.point <= 0 or not math.isfinite(r.forecast_se_log):
            continue
        half = factor * r.forecast_se_log
        new_low = r.point * math.exp(-_Z_90 * half)
        new_high = r.point * math.exp(+_Z_90 * half)
        if new_low <= r.actual <= new_high:
            n_in += 1
    return n_in / len(residuals) if residuals else float("nan")


def _bisect_se_factor(
    residuals: Sequence[BlsFoldResidual],
    target_low: float = COVERAGE_LOWER_BOUND,
    target_high: float = COVERAGE_UPPER_BOUND,
    max_iter: int = 30,
    tol: float = 5e-3,
) -> tuple[float, float]:
    """Bisection on the SE multiplicative factor to land coverage in band.

    Wraps the shared `bisect_for_target_coverage` algorithm with this
    module's log-space `_coverage_at_factor` as the coverage function.
    Returns (factor, achieved_coverage).
    """
    from ..acs.calibration_common import bisect_for_target_coverage
    return bisect_for_target_coverage(
        coverage_fn=lambda f: _coverage_at_factor(residuals, f),
        target_low=target_low, target_high=target_high,
        max_iter=max_iter, tol=tol,
    )


# ---------------------------------------------------------------------------
# Strata grouping
# ---------------------------------------------------------------------------

def _group_residuals(
    residuals: Sequence[BlsFoldResidual],
) -> dict[tuple[str, str, str], list[BlsFoldResidual]]:
    """Group residuals into exact (series, h_bucket, vol_regime) cells."""
    groups: dict[tuple[str, str, str], list[BlsFoldResidual]] = {}
    for r in residuals:
        key = (r.series_id, r.h_bucket, r.vol_regime)
        groups.setdefault(key, []).append(r)
    return groups


def _marginalise_residuals(
    residuals: Sequence[BlsFoldResidual],
    over: str,
) -> dict[tuple[str, str, str], list[BlsFoldResidual]]:
    """Re-group residuals after marginalising one or more axes.

    `over` ∈ {"vol", "h_vol", "all"}:
      * "vol"   — collapse vol_regime: (series, h_bucket, "*")
      * "h_vol" — collapse h_bucket and vol_regime: (series, "*", "*")
      * "all"   — collapse everything: ("*", "*", "*")
    """
    out: dict[tuple[str, str, str], list[BlsFoldResidual]] = {}
    for r in residuals:
        if over == "vol":
            key = (r.series_id, r.h_bucket, WILDCARD)
        elif over == "h_vol":
            key = (r.series_id, WILDCARD, WILDCARD)
        elif over == "all":
            key = (WILDCARD, WILDCARD, WILDCARD)
        else:
            raise ValueError(f"unknown marginalisation: {over!r}")
        out.setdefault(key, []).append(r)
    return out


def _all_groupings(
    residuals: Sequence[BlsFoldResidual],
) -> dict[tuple[str, str, str], list[BlsFoldResidual]]:
    """Concatenate exact + marg-vol + marg-h-vol + global groupings."""
    groups: dict[tuple[str, str, str], list[BlsFoldResidual]] = {}
    groups.update(_group_residuals(residuals))
    groups.update(_marginalise_residuals(residuals, "vol"))
    groups.update(_marginalise_residuals(residuals, "h_vol"))
    groups.update(_marginalise_residuals(residuals, "all"))
    return groups


# ---------------------------------------------------------------------------
# Bias estimation
# ---------------------------------------------------------------------------

def _estimate_bias_records(
    residuals: Sequence[BlsFoldResidual],
    n_threshold: int,
    bias_clamp_log: float,
) -> list[StrataRecord]:
    """Compute geometric bias `b = mean(log(point/actual))` per strata cell.

    Records are emitted at every aggregation level (exact, vol-marg,
    h+vol-marg, global) so the consumer's fallback chain has a value
    to find at any granularity.

    Cells with `n_folds < n_threshold` get applied bias 0.0 (the floor),
    but their raw bias is preserved in the `extra["b_raw"]` field for
    diagnostic purposes.

    Records use the ACS strata schema with `pop_bucket=series_id` and
    `h_bucket=combined h+vol bucket label` so they round-trip through
    `index_records()` without changes. Specifically:

      * StrataRecord.indicator  = "" (not used; series goes in pop_bucket)
      * StrataRecord.method     = "" (BLS has only one "method")
      * StrataRecord.pop_bucket = series_id (or WILDCARD when marginalised)
      * StrataRecord.h_bucket   = "{h_bucket}|{vol_regime}" combined string

    The combined h+vol label is parsed at lookup time. This is a slight
    abuse of the StrataRecord schema but reuses the indexed lookup with
    no schema-version changes — the alternative was to add a third
    dimension field, which would have rippled into the ACS code.
    """
    records: list[StrataRecord] = []
    for key, items in _all_groupings(residuals).items():
        series_id, h_bucket, vol_regime = key
        log_ratios: list[float] = []
        for r in items:
            if r.actual <= 0 or r.point <= 0:
                continue
            try:
                lr = math.log(r.point / r.actual)
            except ValueError:
                continue
            if math.isfinite(lr):
                log_ratios.append(lr)
        n = len(log_ratios)
        b_raw = sum(log_ratios) / n if n > 0 else 0.0
        if n < n_threshold:
            b_applied = 0.0
            clamped = 0.0
        else:
            b_applied = max(-bias_clamp_log, min(bias_clamp_log, b_raw))
            clamped = 1.0 if abs(b_applied - b_raw) > 1e-12 else 0.0

        records.append(StrataRecord(
            indicator="",  # BLS calibration: indicator not used at this level
            method="",
            pop_bucket=series_id,
            h_bucket=f"{h_bucket}|{vol_regime}",
            value=b_applied,
            n_folds=n,
            extra={"b_raw": b_raw, "clamped": clamped},
        ))
    return records


# ---------------------------------------------------------------------------
# SE inflator + per-cell records
# ---------------------------------------------------------------------------

def _bisect_per_cell_records(
    residuals: Sequence[BlsFoldResidual],
    n_threshold: int,
    base_inflator: float = 1.0,
) -> tuple[list[StrataRecord], list[StrataRecord], list[StrataRecord]]:
    """Per-cell SE bisection on bias-corrected residuals.

    Returns three lists of StrataRecords:
      - se_records: absolute κ per cell (factor × base_inflator).
      - cov_pre_records: empirical CI90 coverage at κ=base_inflator.
      - cov_post_records: empirical coverage after applying the
        bisected factor.

    Same fallback structure as the bias records (exact / vol-marg /
    h+vol-marg / global).
    """
    se_recs: list[StrataRecord] = []
    cov_pre_recs: list[StrataRecord] = []
    cov_post_recs: list[StrataRecord] = []

    for key, items in _all_groupings(residuals).items():
        series_id, h_bucket, vol_regime = key
        n = len(items)
        cov_pre = _coverage_at_factor(items, 1.0)
        if n < n_threshold or not math.isfinite(cov_pre):
            factor, cov_post = 1.0, cov_pre
        else:
            factor, cov_post = _bisect_se_factor(items)

        absolute_kappa = factor * base_inflator
        composite_h = f"{h_bucket}|{vol_regime}"
        se_recs.append(StrataRecord(
            indicator="", method="",
            pop_bucket=series_id, h_bucket=composite_h,
            value=absolute_kappa, n_folds=n,
            extra={
                "factor": factor,
                "coverage_pre": cov_pre if math.isfinite(cov_pre) else 0.0,
                "coverage_post": cov_post if math.isfinite(cov_post) else 0.0,
            },
        ))
        cov_pre_recs.append(StrataRecord(
            indicator="", method="",
            pop_bucket=series_id, h_bucket=composite_h,
            value=cov_pre if math.isfinite(cov_pre) else 0.0,
            n_folds=n,
        ))
        cov_post_recs.append(StrataRecord(
            indicator="", method="",
            pop_bucket=series_id, h_bucket=composite_h,
            value=cov_post if math.isfinite(cov_post) else 0.0,
            n_folds=n,
        ))
    return se_recs, cov_pre_recs, cov_post_recs


def _rmse_records(
    residuals: Sequence[BlsFoldResidual],
    n_threshold: int,
) -> list[StrataRecord]:
    """Per-cell RMSE-pct on the (bias-corrected) residuals."""
    out: list[StrataRecord] = []
    for key, items in _all_groupings(residuals).items():
        series_id, h_bucket, vol_regime = key
        sq: list[float] = []
        for r in items:
            if r.actual <= 0:
                continue
            sq.append(((r.point - r.actual) / r.actual) ** 2)
        rmse = math.sqrt(sum(sq) / len(sq)) if sq else float("nan")
        out.append(StrataRecord(
            indicator="", method="",
            pop_bucket=series_id, h_bucket=f"{h_bucket}|{vol_regime}",
            value=rmse if math.isfinite(rmse) else 0.0,
            n_folds=len(items),
        ))
    return out


# ---------------------------------------------------------------------------
# Anchor enumeration + volatility classification
# ---------------------------------------------------------------------------

def _enumerate_anchors(
    series: list[dict],
    horizons_months: Sequence[int],
    min_anchor_index: int = 6,
) -> list[tuple[int, int]]:
    """List (anchor_index, h_months) pairs that have valid actuals.

    `min_anchor_index` (default 6) requires at least 6 prior observations
    so the smoother and residual std have something to fit. Targets are
    looked up by (anchor_year, anchor_month + h_months) within the series;
    folds whose target index doesn't exist are dropped.
    """
    if len(series) < min_anchor_index + 1:
        return []
    ordered = sorted(series, key=lambda p: (p["year"], int(p["period"][1:])))
    period_index: dict[tuple[int, int], int] = {
        (p["year"], int(p["period"][1:])): i
        for i, p in enumerate(ordered)
    }

    pairs: list[tuple[int, int]] = []
    for i in range(min_anchor_index, len(ordered)):
        anchor = ordered[i]
        a_year = anchor["year"]
        a_month = int(anchor["period"][1:])
        for h in horizons_months:
            t_year = a_year + (a_month + h - 1) // 12
            t_month = ((a_month + h - 1) % 12) + 1
            if (t_year, t_month) in period_index:
                pairs.append((i, h))
    return pairs


def _rolling_24mo_return_sd(
    ordered: list[dict],
    end_index: int,
    window_months: int = 24,
) -> float | None:
    """Rolling SD of monthly log-returns in the [end-window, end] window.

    Returns None when the window contains <3 valid pairs. Used as the
    volatility metric for vol_regime classification.
    """
    if end_index < 2:
        return None
    start_year = ordered[end_index]["year"] - (window_months // 12)
    start_month = int(ordered[end_index]["period"][1:])
    rates: list[float] = []
    for i in range(1, end_index + 1):
        prev = ordered[i - 1]
        curr = ordered[i]
        prev_y = prev["year"]
        prev_m = int(prev["period"][1:])
        curr_y = curr["year"]
        curr_m = int(curr["period"][1:])
        if (curr_y, curr_m) > (start_year, start_month):
            months = (curr_y - prev_y) * 12 + (curr_m - prev_m)
            if months > 0 and prev["value"] > 0 and curr["value"] > 0:
                try:
                    rate = math.log(curr["value"] / prev["value"]) / months
                except ValueError:
                    continue
                if math.isfinite(rate):
                    rates.append(rate)
    if len(rates) < 3:
        return None
    return statistics.pstdev(rates)


# ---------------------------------------------------------------------------
# Main calibration entry point
# ---------------------------------------------------------------------------

def run_stratified_bls_calibration(
    cpi_data: dict,
    horizons_months: Sequence[int] = (2, 4, 6, 8, 12),
    min_anchor_index: int = 6,
    n_threshold: int = DEFAULT_N_THRESHOLD,
    bias_clamp_log: float = DEFAULT_BIAS_CLAMP_LOG,
    phi: float = PROJ_DAMPING,
    cap: float = PROJ_MONTHLY_CAP,
    max_pairs: int | None = None,
) -> dict:
    """Run stratified BLS calibration over the supplied CPI panel.

    Parameters
    ----------
    cpi_data : dict[series_id, list[obs]]
        Each obs dict has `{year, period, value}` (BLS shape).
    horizons_months : sequence of integer horizons (months ahead).
        Default covers the bimonthly Honolulu cadence (2, 4, 6, 8) plus
        a 12-month diagnostic horizon.
    min_anchor_index : minimum index in the series to start emitting
        folds. Ensures each fold has enough prior observations for the
        smoother and residual variance estimate. Default 6 (~6 months).
    n_threshold : minimum folds per cell for an applied bias / κ override.
    bias_clamp_log : symmetric clamp on bias in log space (±log(1.10)).
    phi, cap, max_pairs : passed through to `project_forward_full`.

    Returns
    -------
    dict — schema_version=1 BLS calibration payload with strata records,
    marginalised v0 fallback tables, and per-series volatility thresholds.
    """
    # ---- Pass 0: per-series rolling-24-month return SD distribution ----
    # The vol_regime threshold is the MEDIAN across all anchor windows for
    # that series — different for rent (low-vol) vs gasoline (high-vol).
    vol_thresholds: dict[str, float] = {}
    series_anchor_vols: dict[str, dict[int, float]] = {}

    for series_id, points in cpi_data.items():
        if not points:
            continue
        ordered = sorted(points, key=lambda p: (p["year"], int(p["period"][1:])))
        per_anchor_vols: dict[int, float] = {}
        all_vols: list[float] = []
        for i in range(min_anchor_index, len(ordered)):
            v = _rolling_24mo_return_sd(ordered, i)
            if v is not None and math.isfinite(v):
                per_anchor_vols[i] = v
                all_vols.append(v)
        if all_vols:
            vol_thresholds[series_id] = statistics.median(all_vols)
            series_anchor_vols[series_id] = per_anchor_vols

    # ---- Build fold residuals ----
    fold_residuals: list[BlsFoldResidual] = []
    for series_id, points in cpi_data.items():
        if not points:
            continue
        ordered = sorted(points, key=lambda p: (p["year"], int(p["period"][1:])))
        threshold = vol_thresholds.get(series_id)
        anchor_vols = series_anchor_vols.get(series_id, {})

        anchors = _enumerate_anchors(ordered, horizons_months, min_anchor_index)
        for anchor_idx, h_months in anchors:
            anchor = ordered[anchor_idx]
            anchor_year = anchor["year"]
            anchor_month = int(anchor["period"][1:])
            target_year = anchor_year + (anchor_month + h_months - 1) // 12
            target_month = ((anchor_month + h_months - 1) % 12) + 1

            # Find target observation
            actual_obs = next(
                (p for p in ordered
                 if p["year"] == target_year
                 and int(p["period"][1:]) == target_month),
                None,
            )
            if actual_obs is None:
                continue
            actual = float(actual_obs["value"])
            if actual <= 0:
                continue

            # Truncate training data to anchor
            train = ordered[: anchor_idx + 1]
            if len(train) < 2:
                continue

            # Run uncalibrated projection (κ=1.0 so the bisection sees
            # the raw SE)
            try:
                proj = project_forward_full(
                    train,
                    target_date=date(target_year, target_month, 1),
                    phi=phi, cap=cap, max_pairs=max_pairs,
                    inflator=1.0,
                )
            except (ValueError, ZeroDivisionError):
                continue

            if proj.value <= 0 or not math.isfinite(proj.forecast_se_log):
                continue

            # Build CI bounds in log space at κ=1.0
            half = _Z_90 * proj.forecast_se_log
            ci_lo = proj.value * math.exp(-half)
            ci_hi = proj.value * math.exp(+half)

            h_bucket = h_bucket_for_months(h_months)
            vol_regime = vol_regime_for_value(
                anchor_vols.get(anchor_idx), threshold,
            )

            fold_residuals.append(BlsFoldResidual(
                series_id=series_id,
                anchor_year=anchor_year,
                anchor_month=anchor_month,
                target_year=target_year,
                target_month=target_month,
                h_months=h_months,
                h_bucket=h_bucket,
                vol_regime=vol_regime,
                actual=actual,
                point=proj.value,
                residual_log_std=proj.residual_log_std,
                forecast_se_log=proj.forecast_se_log,
                ci90_low=ci_lo,
                ci90_high=ci_hi,
            ))

    # ---- Pass A: bias estimation ----
    bias_records = _estimate_bias_records(
        fold_residuals, n_threshold, bias_clamp_log,
    )

    # ---- Pass A.5: apply bias to fold residuals in memory ----
    bias_index = index_records(
        records=bias_records, n_threshold=n_threshold, floor_value=0.0,
    )
    bias_corrected: list[BlsFoldResidual] = []
    for r in fold_residuals:
        composite_h = f"{r.h_bucket}|{r.vol_regime}"
        rec, _src = bias_index("", "", r.series_id, composite_h)
        b = rec.value
        bias_corrected.append(_apply_bias_to_residual(r, b))

    # ---- Pass B: per-cell SE bisection on bias-corrected residuals ----
    se_records, cov_pre_records, cov_post_records = _bisect_per_cell_records(
        bias_corrected, n_threshold, base_inflator=1.0,
    )

    # ---- Pass C: per-cell RMSE ----
    rmse_records = _rmse_records(bias_corrected, n_threshold)

    # ---- Build marginalised tables for v0 back-compat ----
    rmse_global: dict[str, float] = {}
    cov_global: dict[str, float] = {}
    se_global: dict[str, float] = {}
    for r in rmse_records:
        if r.pop_bucket != WILDCARD and r.h_bucket == f"{WILDCARD}|{WILDCARD}":
            rmse_global[r.pop_bucket] = r.value
    for r in cov_pre_records:
        if r.pop_bucket != WILDCARD and r.h_bucket == f"{WILDCARD}|{WILDCARD}":
            cov_global[r.pop_bucket] = r.value
    for r in se_records:
        if r.pop_bucket != WILDCARD and r.h_bucket == f"{WILDCARD}|{WILDCARD}":
            se_global[r.pop_bucket] = r.value

    return {
        "schema_version": 1,
        "kind": "bls_calibration",
        "run_date": date.today().isoformat(),
        "horizons_months": list(horizons_months),
        "vol_regime_thresholds": vol_thresholds,
        "phi": phi,
        "cap": cap,
        # Strata records (the v3-style canonical layer)
        "strata_records": {
            "rmse": [record_to_dict(r) for r in rmse_records],
            "coverage_pre": [record_to_dict(r) for r in cov_pre_records],
            "coverage_post": [record_to_dict(r) for r in cov_post_records],
            "se_inflator": [record_to_dict(r) for r in se_records],
            "bias": [record_to_dict(r) for r in bias_records],
        },
        # Marginalised tables for legacy / coarse consumers
        "rmse_by_series": rmse_global,
        "ci90_coverage_by_series": cov_global,
        "se_inflator_by_series": se_global,
        # Diagnostic dump (stripped at write time by `write_bls_calibration`)
        "fold_residuals": [
            {
                "series_id": r.series_id, "h_months": r.h_months,
                "h_bucket": r.h_bucket, "vol_regime": r.vol_regime,
                "actual": r.actual, "point": r.point,
                "anchor_year": r.anchor_year, "anchor_month": r.anchor_month,
                "target_year": r.target_year, "target_month": r.target_month,
                "forecast_se_log": r.forecast_se_log,
                "in_ci": int(r.ci90_low <= r.actual <= r.ci90_high),
            }
            for r in fold_residuals
        ],
    }


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def write_bls_calibration(payload: dict, path: Path) -> None:
    """Persist BLS calibration JSON. Drops `fold_residuals` from disk."""
    import json

    DROPPED = {"fold_residuals"}
    summary = {k: v for k, v in payload.items() if k not in DROPPED}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w") as f:
        json.dump(summary, f, indent=2, sort_keys=True)
    tmp.replace(path)


def load_bls_calibration(path: Path | None = None) -> dict | None:
    """Load a BLS calibration JSON. Returns None on absence/error."""
    import json
    import sys

    if path is None:
        path = (
            Path(__file__).resolve().parent.parent
            / "data" / "anchors" / "bls_calibration.json"
        )
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[bls_calibration] load failed ({exc}); falling back to default κ",
              file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# Runtime lookup helpers for the v3 calibration JSON
# ---------------------------------------------------------------------------
#
# The strata records use a composite `h_bucket|vol_regime` label inside
# `StrataRecord.h_bucket` to keep the schema flat. Resolving a lookup
# at projection time requires walking the BLS-specific fallback chain:
#
#     exact (series, h, vol)  ->  vol_marg (series, h, *)
#     ->  h+vol_marg (series, *, *)  ->  global (*, *, *)  ->  floor
#
# This is a slightly different chain from the ACS path because BLS has
# three stratification axes versus ACS's four; the trade-off is worth it
# (we kept the StrataRecord schema unchanged across both modules).

LookupResult = tuple[float, str]
"""(value, source) — source ∈ {"exact", "vol_marg", "h_vol_marg", "global", "floor"}."""


def _records_to_dict(records: Sequence[dict | StrataRecord]) -> dict[tuple[str, str, str], dict]:
    """Index strata records by (series, h_bucket, vol_regime).

    Accepts either raw dicts (as loaded from JSON) or `StrataRecord`
    objects, normalising both to dict-of-fields for the lookup.
    """
    by_key: dict[tuple[str, str, str], dict] = {}
    for raw in records:
        if isinstance(raw, StrataRecord):
            r = {
                "indicator": raw.indicator, "method": raw.method,
                "pop_bucket": raw.pop_bucket, "h_bucket": raw.h_bucket,
                "value": raw.value, "n_folds": raw.n_folds,
                "extra": dict(raw.extra),
            }
        else:
            r = raw
        composite_h = r["h_bucket"]
        if "|" in composite_h:
            h, vol = composite_h.split("|", 1)
        else:
            # WILDCARD-only encoding for global cells written outside the
            # stratification scheme; treat as h=*, vol=*.
            h, vol = WILDCARD, WILDCARD
        key = (r["pop_bucket"], h, vol)
        existing = by_key.get(key)
        if existing is None or r["n_folds"] > existing["n_folds"]:
            by_key[key] = r
    return by_key


def lookup_bls_strata(
    records: Sequence[dict | StrataRecord],
    series_id: str,
    h_bucket: str,
    vol_regime: str,
    n_threshold: int = DEFAULT_N_THRESHOLD,
    floor_value: float = 0.0,
) -> LookupResult:
    """Walk the BLS strata fallback chain.

    Returns `(value, source)`. The `source` field labels which level of
    the chain qualified — useful for the projection's `notes` audit trail.
    """
    by_key = _records_to_dict(records)

    candidates: list[tuple[tuple[str, str, str], str]] = [
        ((series_id, h_bucket, vol_regime), "exact"),
        ((series_id, h_bucket, WILDCARD), "vol_marg"),
        ((series_id, WILDCARD, WILDCARD), "h_vol_marg"),
        ((WILDCARD, WILDCARD, WILDCARD), "global"),
    ]
    for key, src in candidates:
        r = by_key.get(key)
        if r is not None and r["n_folds"] >= n_threshold:
            return (float(r["value"]), src)
    return (float(floor_value), "floor")


def resolve_kappa_and_bias(
    calibration: dict | None,
    series_id: str,
    h_bucket: str,
    vol_regime: str,
    n_threshold: int = DEFAULT_N_THRESHOLD,
    kappa_floor: float = 1.0,
    bias_floor: float = 0.0,
) -> tuple[float, float, str, str]:
    """Resolve (κ, b, κ_source, b_source) from a v3 BLS calibration dict.

    When `calibration` is None or missing the strata_records key, returns
    the floor values with source "floor". Used by `compute_cpi_ratio` to
    apply per-cell calibration to the raw projection.
    """
    if calibration is None:
        return (kappa_floor, bias_floor, "floor", "floor")
    sr = calibration.get("strata_records", {})
    se_records = sr.get("se_inflator", [])
    bias_records = sr.get("bias", [])
    kappa, k_src = lookup_bls_strata(
        se_records, series_id, h_bucket, vol_regime,
        n_threshold=n_threshold, floor_value=kappa_floor,
    )
    b, b_src = lookup_bls_strata(
        bias_records, series_id, h_bucket, vol_regime,
        n_threshold=n_threshold, floor_value=bias_floor,
    )
    return (kappa, b, k_src, b_src)


__all__ = [
    "BlsFoldResidual",
    "DEFAULT_BIAS_CLAMP_LOG",
    "COVERAGE_LOWER_BOUND",
    "COVERAGE_UPPER_BOUND",
    "H_BUCKETS_BLS",
    "VOL_REGIMES",
    "h_bucket_for_months",
    "vol_regime_for_value",
    "run_stratified_bls_calibration",
    "write_bls_calibration",
    "load_bls_calibration",
    "lookup_bls_strata",
    "resolve_kappa_and_bias",
]
