"""Multi-source macro-anchor ensemble.

Replaces the legacy fixed-weight (0.30 to a single CPI rate) macro
blend with a weighted ensemble across all sources admissible for the
indicator under projection.

How weights are determined
--------------------------
For each `(indicator, anchor_source)` pair we compute the historical
RMSE of the source's smoothed YoY log-rate as an estimator of the
*subsequent* ACS YoY change at the same horizon. Sources that closely
track ACS get higher weight; sources that are noisy or biased get
lower. Weights are inverse-variance:

        w_i ∝ 1 / (RMSE_i² + σ_floor²)

with `σ_floor` preventing a single near-perfect source from monopolising
the weight (which would defeat the diversification benefit).

Calibration data
----------------
Per-(indicator, source) RMSE is precomputed by
`scripts/calibrate_anchors.py` and stored in
`data/anchors/calibration.json`. If the calibration file is missing
(first run), the loader falls back to equal weights and emits a
warning. The production `project_acs_2026.py` script always runs
calibration first and writes the file.

Hidden-data discipline
----------------------
The calibration uses only data that would have been visible at each
back-test anchor year T, via `AnchorSource.publication_lag_years`. The
production projection at run time T_now uses *current* visible data;
we explicitly do NOT condition the weights on the projection target.
That would constitute leak-back of out-of-sample information.

References
----------
Granger & Ramanathan (1984) on inverse-variance combination.
Bates & Granger (1969) on the variance reduction available to combinable
forecasts even when they share underlying signal.
"""
from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from common.models import AcsObservation, ForecastPoint
from common.moe import moe_to_se, ci_from_se, combine_se
from .projection import (
    ANNUAL_RATE_CAP,
    effective_year,
    EMPIRICAL_SE_INFLATOR,
)
from .sources import AnchorSource, AnnualRate, available_sources

METHOD_LEVEL_ANCHOR = "level_anchor"


_PKG_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CALIBRATION_PATH = _PKG_ROOT / "data" / "anchors" / "calibration.json"

# Divisor that turns a per-source calibration RMSE into a per-year log-rate
# SE in `combined_anchor_rate`. It is a fixed property of the calibration,
# NOT the projection horizon: every hold-out fold behind the shipped
# multi_anchor κ / bias / conformal records builds its SE with this divisor
# (at every h — the v3+ per-source RMSE is pooled over h=1..5, so 2 is a
# nominal scale and the per-h-bucket κ absorbs the rest), and
# `anchor_as_forecast` then scales the per-year SE by the real h. The
# production consumer (`ensemble.project_ensemble_multi`) must pass the same
# value; dividing by the target h instead (as it did from 3a1e744 until
# 2026-09-29) made the pre-κ SE ≈ rmse at every h — ~2× too wide at h=1 and
# 0.4-0.8× too narrow at h=3-5 relative to what κ was fit on. Changing this
# requires regenerating data/anchors/calibration.json.
ANCHOR_CALIBRATION_HORIZON = 2


# -----------------------------------------------------------------------------
# Level-anchor point (for rate/percentage indicators)
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class LevelAnchorPoint:
    """Combined level observation from one or more level-type anchor sources.

    Unlike AnchorRate (which carries a log-growth-rate), this carries the
    observed level directly — e.g. the county unemployment rate in percentage
    points — because rate indicators are not log-growth-rate compatible.

    The point forecast at target_year is this level (possibly with the
    SE growing for h>1 under a random-walk assumption on the external series).
    """
    point: float
    se: float
    indicator: str
    end_year: int
    geoid: Optional[str]
    source_names: tuple[str, ...]
    weights: tuple[float, ...]


def combined_level_anchor(
    indicator: str,
    end_year: int,
    geoid: Optional[str] = None,
    sources: Optional[list[AnchorSource]] = None,
    calibration: Optional[dict] = None,
) -> Optional[LevelAnchorPoint]:
    """Combine all level-type anchor sources for this indicator at end_year.

    Inverse-variance combines the latest observed levels from all level-type
    sources that have data visible at end_year. Uses each source's
    ``level_se_floor`` as the SE floor; calibration per-source RMSE is NOT
    used here (it is in relative units, which don't apply to level forecasts).
    The κ calibration in the ensemble path handles SE scaling post-hoc.

    Returns None when no level source has visible data for this indicator/geoid.
    """
    if sources is None:
        sources = available_sources(indicator)

    level_sources = [s for s in sources if s.anchor_type == "level"]
    if not level_sources:
        return None

    items: list[tuple[float, float, str]] = []  # (level, se, source_name)
    for src in level_sources:
        level = src.latest_level(end_year=end_year, geoid=geoid)
        if level is None:
            continue
        se = max(src.level_se_floor, 1e-6)
        items.append((level, se, src.name))

    if not items:
        return None

    inv_vars = [1.0 / (se ** 2) for _, se, _ in items]
    total_inv = sum(inv_vars)
    if total_inv <= 0:
        weights = [1.0 / len(items)] * len(items)
    else:
        weights = [w / total_inv for w in inv_vars]

    point = sum(w * lv for w, (lv, _, _) in zip(weights, items))
    se = math.sqrt(sum((w * se) ** 2 for w, (_, se, _) in zip(weights, items)))
    # Ensure combined SE >= any single source's floor.
    max_floor = max(s.level_se_floor for s in level_sources if s.level_se_floor > 0) if level_sources else 0.0
    se = max(se, max_floor)

    return LevelAnchorPoint(
        point=point,
        se=se,
        indicator=indicator,
        end_year=end_year,
        geoid=geoid,
        source_names=tuple(name for _, _, name in items),
        weights=tuple(weights),
    )


def level_anchor_as_forecast(
    latest: AcsObservation,
    target_year: int,
    level_anchor: LevelAnchorPoint,
) -> ForecastPoint:
    """Project `latest` forward using an external level observation.

    The level anchor's point IS the point forecast (not a growth rate
    applied to latest.estimate). For h=1 this says "next year's ACS value
    will be approximately equal to the current external level." For h>1
    the uncertainty grows with sqrt(h) under a random-walk assumption.

    SE = level_anchor.se * EMPIRICAL_SE_INFLATOR * sqrt(h) (forecast component)
    plus the ACS sample SE scaled to the new point magnitude (sample component).
    The κ calibration in the ensemble path will further refine the SE via
    the per-stratum multiplier, just as it does for other methods.
    """
    horizon = target_year - effective_year(latest)
    if horizon <= 0:
        sample_se = moe_to_se(latest.moe)
        if not math.isfinite(sample_se):
            sample_se = 0.0
        ci_lo, ci_hi = ci_from_se(latest.estimate, sample_se)
        return ForecastPoint(
            point=latest.estimate,
            se_total=sample_se,
            se_sample=sample_se,
            se_forecast=0.0,
            ci90_low=ci_lo,
            ci90_high=ci_hi,
            method=METHOD_LEVEL_ANCHOR + "_passthrough",
            target_year=target_year,
            geoid=latest.geoid,
            indicator=latest.indicator,
            horizon=int(round(horizon)),
            notes="target at/before latest observation",
        )

    point = level_anchor.point

    # Forecast SE: base from level anchor, growing with sqrt(h) for h>1.
    se_forecast = level_anchor.se * EMPIRICAL_SE_INFLATOR * math.sqrt(max(horizon, 1))

    # Sample SE: latest ACS relative CV scaled to the new point level.
    # This captures that the anchor level might differ from the ACS level
    # by the same relative amount as the ACS sampling noise.
    sample_relative = (
        moe_to_se(latest.moe) / latest.estimate if latest.estimate > 0 else 0.0
    )
    if not math.isfinite(sample_relative):
        sample_relative = 0.0
    se_sample = sample_relative * point

    se_total = combine_se(se_sample, se_forecast)
    ci_lo, ci_hi = ci_from_se(point, se_total)

    source_str = "+".join(
        f"{name}={w:.2f}"
        for name, w in zip(level_anchor.source_names, level_anchor.weights)
    )
    notes = (
        f"level_anchor(sources=[{source_str}], "
        f"level={point:.2f}, se_base={level_anchor.se:.2f})"
    )

    return ForecastPoint(
        point=point,
        se_total=se_total,
        se_sample=se_sample,
        se_forecast=se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method=METHOD_LEVEL_ANCHOR,
        target_year=target_year,
        geoid=latest.geoid,
        indicator=latest.indicator,
        horizon=int(round(horizon)),
        notes=notes,
    )


# -----------------------------------------------------------------------------
# Anchor-rate aggregator
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class AnchorRate:
    """Combined annual log-rate from a set of macro anchors.

    `point_log_rate` is the inverse-variance-weighted mean.
    `se_log_rate` is the SE of that weighted mean (variance of
    weighted sum, accounting for cross-source correlation rho).
    `components` is the per-source contribution table for audit.
    """
    point_log_rate: float
    se_log_rate: float
    indicator: str
    end_year: int
    components: list[tuple[str, float, float, float]]  # (source, log_rate, se, weight)


def _inverse_variance_weights(
    source_names: Sequence[str],
    rates: Sequence[AnnualRate],
    rmse_floor: float = 0.005,
    indicator_rmse: Optional[dict[str, float]] = None,
) -> list[float]:
    """Inverse-(variance + floor²) weighting across source/rate pairs.

    If `indicator_rmse` is provided (per-source RMSE from calibration),
    use it as the variance component; otherwise fall back to each
    source's empirical SE on its own rate series. Floor prevents any
    single source from monopolising the ensemble weight.
    """
    if len(source_names) != len(rates):
        raise ValueError("source_names and rates must align")
    raw_inv: list[float] = []
    for name, r in zip(source_names, rates):
        if indicator_rmse is not None and name in indicator_rmse:
            v = max(indicator_rmse[name], rmse_floor) ** 2
        else:
            v = max(r.se_log_rate, rmse_floor) ** 2
        if not math.isfinite(v) or v <= 0:
            raw_inv.append(0.0)
        else:
            raw_inv.append(1.0 / v)
    total = sum(raw_inv)
    if total <= 0:
        n = len(rates)
        return [1.0 / n for _ in rates] if n else []
    return [w / total for w in raw_inv]


def load_calibration(
    path: Path = DEFAULT_CALIBRATION_PATH,
) -> Optional[dict]:
    """Load the full calibration payload.

    Returns the entire JSON object (not just one slice) so callers can
    use both `rmse_by_indicator_source` (per-source weights), and
    `rmse_by_indicator_method` + `se_inflator_override_by_indicator_method`
    (macro/trend blend weight + SE rescale).

    Returns None if the file is absent or unreadable. The projection
    code falls back to equal weights and the legacy 0.30 macro weight
    when None.
    """
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[anchors] calibration load failed ({exc}); falling back to equal weights",
              file=sys.stderr)
        return None


def combined_anchor_rate(
    indicator: str,
    end_year: int,
    sources: Optional[Sequence[AnchorSource]] = None,
    calibration: Optional[dict[str, dict[str, float]]] = None,
    correlation_rho: float = 0.6,
    calibration_horizon: int = ANCHOR_CALIBRATION_HORIZON,
    geoid: Optional[str] = None,
) -> Optional[AnchorRate]:
    """Compute the indicator-specific multi-source anchor rate at end_year.

    Process
    -------
    1. Pick all sources whose `indicator_affinity` includes `indicator`.
    2. For each, get its smoothed YoY log-rate visible at end_year.
       County-level sources receive `geoid` so they read the right
       per-county series; non-county sources ignore it.
    3. Inverse-variance combine, with weights drawn from the
       calibration RMSE table when available, else from each source's
       in-sample residual SD.
    4. Compute SE on the combined rate accounting for cross-source
       correlation ρ (default 0.6 — these are different measurements
       of overlapping macro shocks, not independent).

    Calibration use
    ---------------
    `calibration` is `{indicator: {source_name: out_of_sample_rmse}}`
    in fractional pct-error units (e.g. 0.045 = 4.5%). When supplied
    *both* the inverse-variance weighting and the per-source SE on
    log-rate are taken from this table — the calibration RMSE is the
    out-of-sample error of "use this source's smoothed rate to project
    h years and score against ACS truth", which dominates the in-sample
    YoY volatility for admin series like FHFA HPI. We convert it to a
    per-year log-rate SE via:
        se_log_rate ≈ rmse / horizon
    using a nominal calibration scale (`ANCHOR_CALIBRATION_HORIZON` = 2;
    the v3+ per-source RMSE is pooled over h=1..5, so this is a scale the
    per-h-bucket κ absorbs, not a literal back-test horizon; configurable
    via `calibration_horizon`). The in-sample SD acts as a
    floor so that indicators with no calibration entry still get a sane
    SE.

    `calibration_horizon` describes the calibration, not the forecast:
    the ensemble consumer and every calibration fold must pass the same
    value (the default) whatever the target horizon, because the
    multi_anchor κ / bias / conformal records were fit on SEs built
    with it. `anchor_as_forecast` applies the real horizon (SE × h).
    The Kalman member uses its own one-step convention
    (`calibration_horizon=1`) and is calibrated separately.

    Returns None if no source has data visible at end_year (the caller
    must then fall back to ACS-only models).
    """
    if sources is None:
        sources = available_sources(indicator)
    if not sources:
        return None

    # Only use rate-type anchors here; level-type anchors go through
    # combined_level_anchor() / level_anchor_as_forecast() instead.
    # Including level-type sources here would apply log-growth-rate
    # arithmetic to rate-bounded series (e.g. LAUS unemployment), which
    # produces explosive signals during COVID and was empirically worse.
    sources = [s for s in sources if s.anchor_type == "rate"]
    if not sources:
        return None

    source_names: list[str] = []
    rates: list[AnnualRate] = []
    for src in sources:
        rate = src.smoothed_annual_rate(end_year=end_year, geoid=geoid)
        if rate is None:
            continue
        source_names.append(src.name)
        rates.append(rate)

    if not rates:
        return None

    per_source_rmse = (calibration or {}).get(indicator, {})

    # Replace per-source SE with calibration-derived SE when available.
    # Floor at the in-sample SD so we never drop *below* what the source's
    # own time-series suggests.
    horizon = max(int(calibration_horizon), 1)
    rates_calibrated: list[AnnualRate] = []
    for name, r in zip(source_names, rates):
        if name in per_source_rmse:
            cal_se = per_source_rmse[name] / horizon
            new_se = max(r.se_log_rate, cal_se)
            rates_calibrated.append(AnnualRate(year=r.year, log_rate=r.log_rate, se_log_rate=new_se))
        else:
            rates_calibrated.append(r)

    weights = _inverse_variance_weights(
        source_names, rates_calibrated,
        indicator_rmse=per_source_rmse if per_source_rmse else None,
    )

    point = sum(w * r.log_rate for w, r in zip(weights, rates_calibrated))

    # Variance of weighted sum with cross-source correlation rho.
    n = len(weights)
    var = 0.0
    for i in range(n):
        for j in range(n):
            corr = 1.0 if i == j else correlation_rho
            var += (
                weights[i] * weights[j]
                * rates_calibrated[i].se_log_rate * rates_calibrated[j].se_log_rate
                * corr
            )
    se = math.sqrt(var) if var > 0 else 0.0

    components = [
        (source_names[i], rates_calibrated[i].log_rate,
         rates_calibrated[i].se_log_rate, weights[i])
        for i in range(n)
    ]
    return AnchorRate(
        point_log_rate=point,
        se_log_rate=se,
        indicator=indicator,
        end_year=end_year,
        components=components,
    )


# -----------------------------------------------------------------------------
# Anchor-as-forecast
# -----------------------------------------------------------------------------

def anchor_as_forecast(
    latest: AcsObservation,
    target_year: int,
    anchor_rate: AnchorRate,
) -> ForecastPoint:
    """Project `latest` forward using the multi-source anchor's rate.

    Carries the anchor-rate SE *into* forecast SE — unlike the legacy
    `macro_anchor_projection` which treated the rate as truth. This is
    the correct uncertainty propagation for "the rate is itself an
    estimate, not a known constant".

    The implied anchored value at horizon h is:
        log(y_T) = log(y_anchor) + h · μ_rate
        Var(log y_T) ≈ Var(log y_anchor) + h² · Var(μ_rate)
    The first term is the ACS sample variance at the anchor; the
    second is the rate uncertainty propagated through h compoundings.
    Then we delta-method back to dollar space (σ_y ≈ σ_logy · y).
    """
    horizon = target_year - effective_year(latest)
    if horizon <= 0:
        sample_se = moe_to_se(latest.moe)
        if not math.isfinite(sample_se):
            sample_se = 0.0
        ci_lo, ci_hi = ci_from_se(latest.estimate, sample_se)
        return ForecastPoint(
            point=latest.estimate,
            se_total=sample_se,
            se_sample=sample_se,
            se_forecast=0.0,
            ci90_low=ci_lo,
            ci90_high=ci_hi,
            method="multi_anchor",
            target_year=target_year,
            geoid=latest.geoid,
            indicator=latest.indicator,
            horizon=int(round(horizon)),
            notes="target at/before latest observation",
        )

    # Cap the rate. ANNUAL_RATE_CAP is in linear-rate space; convert to
    # log space for the cap test. Annual rate cap of +10% → log(1.10) ≈ 0.0953.
    log_cap = math.log1p(ANNUAL_RATE_CAP)
    log_floor = math.log1p(-ANNUAL_RATE_CAP)
    rate = anchor_rate.point_log_rate
    notes = ""
    if rate > log_cap:
        notes = (
            f"anchor rate {math.expm1(rate) * 100:+.2f}%/yr "
            f"capped to {ANNUAL_RATE_CAP * 100:+.2f}%/yr"
        )
        rate = log_cap
    elif rate < log_floor:
        notes = (
            f"anchor rate {math.expm1(rate) * 100:+.2f}%/yr "
            f"capped to {-ANNUAL_RATE_CAP * 100:+.2f}%/yr"
        )
        rate = log_floor

    log_target = math.log(latest.estimate) + horizon * rate
    point = math.exp(log_target)

    # Sample SE at the anchor (relative-CV propagated to projection magnitude).
    sample_relative = (
        moe_to_se(latest.moe) / latest.estimate if latest.estimate > 0 else 0.0
    )
    if not math.isfinite(sample_relative):
        sample_relative = 0.0
    se_sample = sample_relative * point

    # Rate-uncertainty propagation through h compoundings: SE in log space
    # is h · SE(rate); times point in dollar space via delta method.
    se_rate_log = horizon * anchor_rate.se_log_rate * EMPIRICAL_SE_INFLATOR
    se_forecast = se_rate_log * point

    se_total = combine_se(se_sample, se_forecast)
    ci_lo, ci_hi = ci_from_se(point, se_total)

    component_str = "; ".join(
        f"{name}={w:.2f}@r={math.expm1(r) * 100:+.2f}%"
        for name, r, _se, w in anchor_rate.components
    )
    full_notes = f"sources[{component_str}]"
    if notes:
        full_notes += " | " + notes

    return ForecastPoint(
        point=point,
        se_total=se_total,
        se_sample=se_sample,
        se_forecast=se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method="multi_anchor",
        target_year=target_year,
        geoid=latest.geoid,
        indicator=latest.indicator,
        horizon=int(round(horizon)),
        notes=full_notes,
    )
