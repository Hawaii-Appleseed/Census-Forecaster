"""Ensemble combiner for ACS projections.

The two base models in `projection.py` (damped log trend + AR(1) on log
diffs) trade off bias and variance differently. Damped trend is more
biased toward "things will continue at a slowing rate" — robust at low
sample sizes. AR(1) on diffs is more responsive but noisier. Combining
them with inverse-variance weights gives a forecast that's at least as
accurate as the better individual model on every walk-forward fold we
tried (see backtests/results/).

Two macro-anchor flavors are supported:

* `project_ensemble(...)` — legacy, takes a single user-supplied
  `macro_annual_rate` and blends at a configurable weight (default
  derived from calibration if available, else 0.30 for backwards
  compatibility with prior tests).
* `project_ensemble_multi(...)` — new in v0.2: builds a multi-source
  anchor via `anchors.combined_anchor_rate(indicator, end_year)`,
  inverse-variance-weighting CPI / PCE / QCEW / HUD FMR / FHFA HPI
  per indicator. Macro/trend blend weight is also derived from
  back-test RMSE — no hardcoded 70/30.

Why a per-indicator calibrated weight beats the hardcoded 0.30
---------------------------------------------------------------
Different indicators have different macro/idiosyncratic ratios. Median
home value is dominated by FHFA HPI (very tight macro signal); median
rent is more idiosyncratic county to county; median income sits in
between. A single 0.30 weight either over-anchors home value (forfeits
macro signal) or under-anchors rent (lets ACS noise drive). The
calibrated weight is `RMSE_trend² / (RMSE_trend² + RMSE_anchor²)`
per (indicator, source-set), which is the *optimal* combination
weight for two independent unbiased estimators (Bates & Granger 1969).

References
----------
Granger & Ramanathan (1984), "Improved methods of combining forecasts."
J. Forecasting. — inverse-variance weights.
Bates & Granger (1969), "The combination of forecasts." OR Quarterly. —
optimal combination weight derivation.
Cleveland Fed WP 22-38r — anchor-blend pattern (used in the existing
`blend_rent_nowcast` in this repo).
"""
from __future__ import annotations

import math
from typing import Sequence

from common.models import AcsObservation, ForecastPoint
from common.moe import moe_to_se, combine_se, ci_from_se
from .projection import (
    project_damped_trend,
    project_ar1_log_diff,
    ANNUAL_RATE_CAP,
    effective_year,
)


def _inverse_variance_weights(forecasts: Sequence[ForecastPoint]) -> list[float]:
    """Inverse-variance combination weights, normalised to sum to 1.

    A model with infinite variance gets weight 0; if all variances are
    zero (degenerate), fall back to equal weights.
    """
    inv = []
    for f in forecasts:
        v = f.se_total ** 2
        if not math.isfinite(v) or v <= 0:
            inv.append(0.0)
        else:
            inv.append(1.0 / v)
    total = sum(inv)
    if total <= 0:
        n = len(forecasts)
        return [1.0 / n for _ in forecasts] if n else []
    return [w / total for w in inv]


def macro_anchor_projection(
    latest: AcsObservation,
    target_year: int,
    annual_growth_rate: float,
    sample_se_floor: float | None = None,
    rate_se_log: float | None = None,
) -> ForecastPoint:
    """Project a single observation forward at an exogenous annual rate.

    Used as the macro-anchor input to ensembles: e.g. project median rent
    forward at the BLS Honolulu rent-CPI-implied annual growth, or
    project median income at the wage-growth implied by Hawaii DLIR
    QCEW data. The caller supplies the rate; this function does the
    compounding, applies the cap, and returns a ForecastPoint with
    sample SE pulled from the input MOE.

    Parameters
    ----------
    rate_se_log : float, optional
        1-sigma standard error of the macro rate **in log space**
        (i.e. the uncertainty in the annual log-growth-rate itself).
        When supplied, the forecast SE is augmented by the delta-method
        propagation of rate uncertainty into level uncertainty:
            se_forecast = |horizon| · rate_se_log · point
        This corrects the previous assumption that the macro rate is
        perfectly known (se_forecast=0.0), which understated CI width
        for long-horizon projections via the legacy ``project_ensemble``
        path.  The production ``project_ensemble_multi`` path goes through
        ``anchor_as_forecast`` (anchors.py) which already propagates
        rate SE — this parameter is for the legacy fixed-rate path only.

    Notes
    -----
    When ``rate_se_log`` is None (default) the function behaves as
    before: se_forecast=0 and se_total = se_sample only.  Callers using
    the legacy ``project_ensemble(macro_annual_rate=...)`` path should
    supply ``rate_se_log`` to get honest forecast intervals; otherwise
    the CI only captures ACS sampling noise and will be too narrow for
    multi-year horizons.
    """
    horizon = target_year - effective_year(latest)
    if horizon <= 0:
        sample_se = moe_to_se(latest.moe)
        ci_lo, ci_hi = ci_from_se(latest.estimate, sample_se)
        return ForecastPoint(
            point=latest.estimate,
            se_total=sample_se if math.isfinite(sample_se) else 0.0,
            se_sample=sample_se if math.isfinite(sample_se) else 0.0,
            se_forecast=0.0,
            ci90_low=ci_lo,
            ci90_high=ci_hi,
            method="macro_anchor",
            target_year=target_year,
            geoid=latest.geoid,
            indicator=latest.indicator,
            horizon=int(round(horizon)),
            notes="target at/before latest observation",
        )

    # Cap and compound.
    rate = max(min(annual_growth_rate, ANNUAL_RATE_CAP), -ANNUAL_RATE_CAP)
    notes = ""
    if rate != annual_growth_rate:
        notes = (
            f"macro rate {annual_growth_rate * 100:+.2f}%/yr "
            f"capped to {rate * 100:+.2f}%/yr"
        )
    point = latest.estimate * ((1.0 + rate) ** horizon)

    # Sample SE scaled to the projection magnitude.
    sample_relative = moe_to_se(latest.moe) / latest.estimate if latest.estimate > 0 else 0.0
    if not math.isfinite(sample_relative):
        sample_relative = 0.0
    se_sample = sample_relative * point
    if sample_se_floor is not None and math.isfinite(sample_se_floor):
        se_sample = max(se_sample, sample_se_floor)

    # Forecast SE from rate uncertainty (delta method: Var(f(r)) ≈ (df/dr)² Var(r)).
    # For f(r) = P₀ · (1+r)^h ≈ P₀ · exp(h·r_log), df/dr_log = h · point.
    # When rate_se_log is None we fall back to se_forecast=0, preserving
    # legacy behaviour but accepting the known under-statement of CI width.
    if rate_se_log is not None and math.isfinite(rate_se_log) and rate_se_log > 0:
        se_forecast = abs(horizon) * rate_se_log * point
        notes_suffix = f"rate_se_log={rate_se_log:.4f}"
        notes = f"{notes}; {notes_suffix}" if notes else notes_suffix
    else:
        se_forecast = 0.0
        if horizon > 2:
            # Warn when se_forecast=0 is likely to materially understate CI
            # for multi-year projections via the legacy fixed-rate path.
            warn = "se_forecast=0(legacy:supply rate_se_log for honest multi-year CI)"
            notes = f"{notes}; {warn}" if notes else warn

    from common.moe import combine_se as _combine_se
    se_total = _combine_se(se_sample, se_forecast)
    ci_lo, ci_hi = ci_from_se(point, se_total)
    return ForecastPoint(
        point=point,
        se_total=se_total,
        se_sample=se_sample,
        se_forecast=se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method="macro_anchor",
        target_year=target_year,
        geoid=latest.geoid,
        indicator=latest.indicator,
        horizon=int(round(horizon)),
        notes=notes,
    )


def combine_forecasts(
    forecasts: Sequence[ForecastPoint],
    target_year: int,
    method_label: str = "ensemble",
) -> ForecastPoint | None:
    """Inverse-variance combine a list of forecasts for the same target.

    All forecasts must agree on geoid, indicator, and target_year — a
    hard validation rather than silent averaging-of-apples-and-oranges.
    The combined SE is computed from the weighted sum's variance, which
    *can* be smaller than any individual SE (that's the value of
    combining). The 90% CI is symmetric around the weighted point.
    """
    forecasts = list(forecasts)
    if not forecasts:
        return None
    geoids = {f.geoid for f in forecasts}
    indicators = {f.indicator for f in forecasts}
    years = {f.target_year for f in forecasts}
    if len(geoids) != 1 or len(indicators) != 1 or len(years) != 1:
        raise ValueError(
            "combine_forecasts: forecasts must share geoid, indicator, target_year"
        )
    weights = _inverse_variance_weights(forecasts)
    point = sum(w * f.point for w, f in zip(weights, forecasts))

    # Variance of weighted sum, treating component forecasts as
    # *correlated*. The two component models (damped trend + AR(1) on
    # log-diffs) consume the same input series and share parameter
    # estimation noise, so the independence assumption (ρ=0) yields an
    # artificially tight combined CI. We use ρ=0.7, a conservative
    # value at the high end of what Tebaldi & Knutti (2007) and the
    # IPCC AR6 multi-model literature derive for "different methods,
    # same training data" forecast pairs. Lower ρ would make the
    # ensemble CI look misleadingly precise; ρ=1 collapses the variance
    # to the weighted sum of component SEs and forfeits the diversity
    # benefit. Walk-forward calibration on the Hawaii panel confirmed
    # 0.7 brings ensemble CI90-coverage into the 88-92% band.
    rho = 0.7
    n = len(forecasts)
    var = 0.0
    for i in range(n):
        for j in range(n):
            corr = 1.0 if i == j else rho
            var += weights[i] * weights[j] * forecasts[i].se_total * forecasts[j].se_total * corr
    se_total = math.sqrt(var) if var > 0 else 0.0

    # Decompose into sample + forecast components by the same weighting
    # for audit; the values won't sum exactly to se_total under the
    # correlation assumption but they show the relative contribution.
    se_sample = math.sqrt(sum((w * f.se_sample) ** 2 for w, f in zip(weights, forecasts)))
    se_forecast = math.sqrt(sum((w * f.se_forecast) ** 2 for w, f in zip(weights, forecasts)))

    ci_lo, ci_hi = ci_from_se(point, se_total)
    horizons = {f.horizon for f in forecasts}
    horizon = horizons.pop() if len(horizons) == 1 else max(horizons)

    component_notes = "; ".join(
        f"{f.method}={w:.2f}" for f, w in zip(forecasts, weights)
    )
    cap_notes = [f.notes for f in forecasts if f.notes]
    notes = f"weights[{component_notes}]"
    if cap_notes:
        notes += " | " + " ; ".join(sorted(set(cap_notes)))

    return ForecastPoint(
        point=point,
        se_total=se_total,
        se_sample=se_sample,
        se_forecast=se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method=method_label,
        target_year=target_year,
        geoid=forecasts[0].geoid,
        indicator=forecasts[0].indicator,
        horizon=horizon,
        notes=notes,
    )


def project_ensemble(
    series_observations: Sequence[AcsObservation],
    target_year: int,
    macro_annual_rate: float | None = None,
    macro_weight: float = 0.30,
) -> ForecastPoint | None:
    """Run the standard ensemble: damped trend + AR(1) (+ optional macro).

    Components
    ----------
    1. damped_log_trend     — always run if there are ≥2 observations.
    2. ar1_log_diff         — run if there are ≥4 observations.
    3. macro_anchor         — run if `macro_annual_rate` is not None.
       Inserted at fixed weight `macro_weight`; the remaining
       (1 − macro_weight) is split across (1)+(2) by inverse variance.

    Returns the ensemble ForecastPoint, or `None` if no component can
    be fit (e.g. a 1-observation series with no macro anchor).
    """
    if not series_observations:
        return None

    components: list[ForecastPoint] = []
    f_damped = project_damped_trend(series_observations, target_year)
    if f_damped is not None:
        components.append(f_damped)
    f_ar1 = project_ar1_log_diff(series_observations, target_year)
    if f_ar1 is not None:
        components.append(f_ar1)

    if not components and macro_annual_rate is None:
        return None

    if macro_annual_rate is not None:
        anchor = macro_anchor_projection(
            series_observations[-1], target_year, macro_annual_rate
        )
        # Two-stage combination: ensemble the trend models first, then
        # blend the anchor at a fixed weight. This matches the
        # `blend_rent_nowcast` 70/30 pattern used elsewhere in this repo.
        if components:
            inner = combine_forecasts(components, target_year, method_label="trend_ensemble")
            if inner is None:
                return anchor
            point = (1 - macro_weight) * inner.point + macro_weight * anchor.point
            # Variance of fixed-weight blend, again with corr=0.5.
            rho = 0.5
            w = [1 - macro_weight, macro_weight]
            ses = [inner.se_total, anchor.se_total]
            var = 0.0
            for i in range(2):
                for j in range(2):
                    corr = 1.0 if i == j else rho
                    var += w[i] * w[j] * ses[i] * ses[j] * corr
            se_total = math.sqrt(var) if var > 0 else 0.0
            se_sample = math.sqrt((w[0] * inner.se_sample) ** 2 + (w[1] * anchor.se_sample) ** 2)
            se_forecast = math.sqrt((w[0] * inner.se_forecast) ** 2 + (w[1] * anchor.se_forecast) ** 2)
            ci_lo, ci_hi = ci_from_se(point, se_total)
            notes = (
                f"macro_blend(macro={macro_weight:.2f}, trend={1 - macro_weight:.2f}); "
                f"inner: {inner.notes}"
            )
            if anchor.notes:
                notes += f"; anchor: {anchor.notes}"
            return ForecastPoint(
                point=point,
                se_total=se_total,
                se_sample=se_sample,
                se_forecast=se_forecast,
                ci90_low=ci_lo,
                ci90_high=ci_hi,
                method="ensemble_macro",
                target_year=target_year,
                geoid=inner.geoid,
                indicator=inner.indicator,
                horizon=inner.horizon,
                notes=notes,
            )
        return anchor

    return combine_forecasts(components, target_year, method_label="ensemble")


# -----------------------------------------------------------------------------
# Multi-source anchor ensemble
# -----------------------------------------------------------------------------

# -----------------------------------------------------------------------------
# Coverage-warning helper
# -----------------------------------------------------------------------------

# Below this pre-override CI90 coverage the model's interval shape is
# fundamentally unsuited to the indicator (e.g. Gaussian CI for a
# mean-reverting unemployment series). The SE override rescales the width,
# but a coverage warning is still warranted so consumers know the CI
# relies entirely on empirical recalibration rather than a correctly
# specified model.
_PRE_OVERRIDE_COVERAGE_WARNING_THRESHOLD: float = 0.82


def _coverage_warning_note(
    indicator: str,
    method: str,
    calibration: dict | None,
) -> str:
    """Return a coverage-warning note when pre-override CI90 is materially below 90%.

    Reads `ci90_coverage_by_indicator_method` (the raw, pre-SE-override
    coverage) from the calibration payload.  If coverage is below
    `_PRE_OVERRIDE_COVERAGE_WARNING_THRESHOLD`, returns a short string
    that callers can append to ``ForecastPoint.notes``.  Empty string
    when coverage is acceptable or calibration is absent.

    The threshold 82% was chosen to flag S2301 (unemployment, ~77% raw
    coverage) and B25064 (gross rent, ~83% raw coverage) while not
    triggering for indicators that approach 85% after κ calibration.
    """
    if calibration is None:
        return ""
    raw_cov = (
        calibration.get("ci90_coverage_by_indicator_method", {})
        .get(indicator, {})
        .get(method)
    )
    if raw_cov is None or not math.isfinite(raw_cov):
        return ""
    if raw_cov < _PRE_OVERRIDE_COVERAGE_WARNING_THRESHOLD:
        return (
            f"coverage_warn:raw_CI90={raw_cov:.1%}<{_PRE_OVERRIDE_COVERAGE_WARNING_THRESHOLD:.0%}"
            f"(CI relies on SE rescaling; shape may not capture tail events)"
        )
    return ""


# -----------------------------------------------------------------------------
# v3 calibration lookup helpers
# -----------------------------------------------------------------------------
#
# The v2 path looked up a single (indicator, method) → kappa override in a
# nested dict at top level of the calibration payload. The v3 path looks up
# (indicator, method, pop_bucket, h_bucket) → kappa, with a fallback chain
# (exact → h-marginalised → globally marginalised → v2 key → global default).
# Both consumers (_apply_se_override, _apply_bias_correction) need the same
# lookup machinery, so factor it here.

# Indicators for which the Kalman state-space member replaces the
# Bates-Granger blend by default (`use_kalman=None` → auto). Promotion
# requires passing BOTH per-indicator ship gates in the Kalman ablation
# (RMSE improvement ≥5% vs best baseline AND raw CI90 coverage in
# [85%, 95%]) — re-verified 2026-08-21 on the current calibration
# machinery (backtests/results/phase_d_kalman_ablation.md):
#   pct_service_occupations  −15.0% RMSE, 90.8% Cov90
#   B25071_001E rent burden  −10.6% RMSE, 93.8% Cov90
# Every other indicator FAILS at least one gate (wholesale verdict:
# HOLD). Do not add an indicator here without a fresh ablation pass.
KALMAN_PROMOTED_INDICATORS: frozenset[str] = frozenset({
    "B25071_001E",
    "pct_service_occupations",
})


# Lazily-loaded bundled calibration panel for the ML ensemble member.
# Mirrors the `populations` lazy-load: callers may pass their own
# panel/populations, but `use_ml=True` alone must be sufficient — the
# bundled panel is the training set the shipped ml_trend calibration
# records were fit on. The sentinel {} marks "tried and unavailable" so
# a missing panel is probed only once per process.
_ML_BUNDLED: dict | None = None
_ML_DEFAULT_MODEL_CACHE: dict = {}


def _load_bundled_ml_panel() -> dict | None:
    global _ML_BUNDLED
    if _ML_BUNDLED is None:
        try:
            from ..scripts.load_calibration_panel import load_panel
            from .ml_features import (
                build_panel_index,
                load_county_data,
                load_market_signals_data,
                load_national_macro_data,
                load_state_data,
            )
            series, pops, _manifest = load_panel()
            # Build the panel WITH the auxiliary channels (BPS/SAIPE/LAUS,
            # screen-gated market signals, national macro, state UI
            # claims) — the shipped
            # ml_trend calibration records were fit on models trained with
            # these features (calibration.py Pass 2b loads the same three),
            # so the production model must see the same columns.
            _ML_BUNDLED = {
                "series": series,
                "populations": pops,
                "panel": build_panel_index(
                    series,
                    county_data=load_county_data(),
                    market_data=load_market_signals_data(),
                    national_data=load_national_macro_data(),
                    state_data=load_state_data(),
                ),
            }
        except Exception:
            _ML_BUNDLED = {}
    return _ML_BUNDLED or None


def _lookup_phi(
    calibration: dict | None,
    indicator: str,
    method_key: str,
    pop_bucket: str | None,
    h_bucket: str | None,
) -> float:
    """Look up the calibrated phi for this cell, falling through to DEFAULT_PHI.

    Uses the same 4-level strata fallback chain as kappa/bias. phi records
    are emitted by the v4 calibration pass (Pass 0 in calibration.py) under
    strata_records["phi"], but are applied only when the payload carries
    `phi_enabled: true` (set by `run_stratified_calibration(enable_phi=True)`).
    The v4 phi ablation failed its acceptance bar (see METHODOLOGY.md §v4),
    so the records ship as diagnostics and the production default is
    DEFAULT_PHI — a payload without the flag (v2/v3, or v4/v5 with phi
    disabled) must project at the same phi its kappa/bias records were
    calibrated under.
    """
    from .projection import DEFAULT_PHI
    from .strata import PHI_N_THRESHOLD
    if calibration is None or not calibration.get("phi_enabled"):
        return DEFAULT_PHI
    # phi strata use PHI_N_THRESHOLD (15), not DEFAULT_N_THRESHOLD (20)
    from .strata import index_records, record_from_dict
    sr = calibration.get("strata_records", {})
    raw = sr.get("phi")
    if not raw:
        return DEFAULT_PHI
    records = [record_from_dict(d) for d in raw]
    lookup_fn = index_records(
        records=records,
        n_threshold=PHI_N_THRESHOLD,
        floor_value=DEFAULT_PHI,
    )
    rec, _src = lookup_fn(indicator, method_key, pop_bucket, h_bucket)
    return rec.value


def _v3_strata_lookup(
    calibration: dict | None,
    kind: str,
    floor_value: float,
):
    """Build an indexed strata lookup over `calibration["strata_records"][kind]`.

    Returns None when the calibration is v2 (no strata_records key) or
    when records for `kind` are absent. Caller falls back to v2 path.
    """
    if calibration is None:
        return None
    sr = calibration.get("strata_records")
    if not sr:
        return None
    raw = sr.get(kind)
    if not raw:
        return None
    from .strata import index_records, record_from_dict, DEFAULT_N_THRESHOLD
    records = [record_from_dict(d) for d in raw]
    return index_records(
        records=records,
        n_threshold=DEFAULT_N_THRESHOLD,
        floor_value=floor_value,
    )


def _apply_se_override(
    fp: ForecastPoint,
    indicator: str,
    method_key: str,
    calibration: dict | None,
    pop_bucket: str | None = None,
    h_bucket: str | None = None,
) -> ForecastPoint:
    """Re-scale a ForecastPoint's *total* SE by the calibrated κ.

    Looks up the appropriate κ via the v3 strata fallback chain when the
    calibration carries v3 records; otherwise uses the v2 single-key
    lookup `calibration["se_inflator_override_by_indicator_method"]`.

    The override is the *absolute* κ value (matching the global
    `EMPIRICAL_SE_INFLATOR=1.30` baked into projection.py); the linear
    factor we multiply `se_total` by is `override / EMPIRICAL_SE_INFLATOR`.
    If the target SE would drop below the irreducible sample SE (negative
    forecast variance), we clamp at se_sample — an honest floor since the
    ACS MOE itself caps how tight any 90% CI can legitimately get.

    Backwards compatible: if `pop_bucket` and `h_bucket` are None and the
    calibration is v2, behaves exactly as the pre-v3 implementation.
    """
    if calibration is None or fp is None:
        return fp

    from .projection import EMPIRICAL_SE_INFLATOR as _SE_INF

    override: float | None = None
    source_label = ""

    # v3 strata lookup if available
    se_lookup = _v3_strata_lookup(calibration, "se_inflator", floor_value=_SE_INF)
    if se_lookup is not None:
        rec, src = se_lookup(indicator, method_key, pop_bucket, h_bucket)
        if src != "floor":
            override = rec.value
            source_label = f"strata={src}"

    # v2 fallback (also used when v3 strata lookup fell through to floor)
    if override is None:
        override = (
            calibration.get("se_inflator_override_by_indicator_method", {})
            .get(indicator, {})
            .get(method_key)
        )
        if override is not None:
            source_label = "v2"

    if override is None or not math.isfinite(override) or override <= 0:
        return fp

    factor = override / _SE_INF
    new_se_total = max(fp.se_total * factor, fp.se_sample)
    forecast_var = max(new_se_total ** 2 - fp.se_sample ** 2, 0.0)
    new_se_forecast = math.sqrt(forecast_var)
    ci_lo, ci_hi = ci_from_se(fp.point, new_se_total)
    notes = fp.notes
    suffix = f"se_override={override:.3f}"
    if source_label:
        suffix += f"({source_label})"
    notes = f"{notes}; {suffix}" if notes else suffix

    # Attach coverage warning when the raw (pre-override) CI90 coverage was
    # materially below 90%, meaning the CI relies entirely on the SE
    # multiplier rather than a correctly specified probability model.
    cov_warn = _coverage_warning_note(indicator, method_key, calibration)
    if cov_warn:
        notes = f"{notes}; {cov_warn}"

    return ForecastPoint(
        point=fp.point,
        se_total=new_se_total,
        se_sample=fp.se_sample,
        se_forecast=new_se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method=fp.method,
        target_year=fp.target_year,
        geoid=fp.geoid,
        indicator=fp.indicator,
        horizon=fp.horizon,
        notes=notes,
    )


def _apply_bias_correction(
    fp: ForecastPoint,
    indicator: str,
    method_key: str,
    calibration: dict | None,
    pop_bucket: str | None = None,
    h_bucket: str | None = None,
) -> ForecastPoint:
    """Apply geometric bias correction to a ForecastPoint.

    Looks up the bias `b = mean(log(point/actual))` for this cell from
    the v3 strata records and applies `point_corrected = point / exp(b)`.
    All SE components and CI bounds rescale by the same factor so the CI
    width tracks the corrected level (in log space, a level shift is
    multiplicative on dollar-space SEs).

    No-op when:
    * calibration is v2 (no `strata_records["bias"]` key)
    * cell falls below n_threshold (lookup returns floor with value=0)
    * bias is exactly 0 (numerical no-op)

    The order of application matters: callers should run this **before**
    `_apply_se_override`. The SE override was calibrated on
    bias-corrected residuals, so applying κ to a still-biased point
    would double-count the level miscalibration in the CI.
    """
    if calibration is None or fp is None:
        return fp
    bias_lookup = _v3_strata_lookup(calibration, "bias", floor_value=0.0)
    if bias_lookup is None:
        return fp
    rec, src = bias_lookup(indicator, method_key, pop_bucket, h_bucket)
    b = rec.value
    if b == 0.0 or not math.isfinite(b):
        return fp
    factor = math.exp(-b)
    new_point = fp.point * factor
    new_se_total = fp.se_total * factor
    new_se_sample = fp.se_sample * factor
    new_se_forecast = fp.se_forecast * factor
    new_ci_low = fp.ci90_low * factor
    new_ci_high = fp.ci90_high * factor
    notes = fp.notes
    suffix = f"bias_corr={b * 100:+.2f}%(strata={src})"
    notes = f"{notes}; {suffix}" if notes else suffix
    return ForecastPoint(
        point=new_point,
        se_total=new_se_total,
        se_sample=new_se_sample,
        se_forecast=new_se_forecast,
        ci90_low=new_ci_low,
        ci90_high=new_ci_high,
        method=fp.method,
        target_year=fp.target_year,
        geoid=fp.geoid,
        indicator=fp.indicator,
        horizon=fp.horizon,
        notes=notes,
    )


def _apply_conformal_interval(
    fp: ForecastPoint,
    indicator: str,
    method_key: str,
    calibration: dict | None,
    se_pre_kappa: float,
    pop_bucket: str | None = None,
    h_bucket: str | None = None,
) -> ForecastPoint:
    """Replace the CI with the split-conformal interval where a record exists.

    The conformal half-width is  q · se_pre_kappa  where q is the
    per-stratum quantile stored in calibration["conformal_quantile_by_stratum"].
    When a stratum record exists, the conformal interval REPLACES the κ-based
    CI — narrower or wider. When no record covers the stratum (n < 20 on the
    calibration folds, or a pre-conformal payload), the κ-based CI stands.

    This replaced the original max(κ_half, conformal_half) "floor" policy
    on 2026-08-21: both half-widths are independently calibrated to 90%,
    so their max systematically over-covers. On the held-out 2022 anchor
    (6,912 folds) the max policy covered 94.3% at mean relative half-width
    24.0%; conformal-primary covered 92.7% at 20.7% — better centered AND
    sharper — while also eliminating the κ-only policy's under-coverage
    tail (cells at 56–84%). The split-conformal guarantee applies to the
    conformal interval itself, not to flooring another interval with it.

    `se_total` is left as the κ-corrected SE (the CI is the conformal
    product; the SE fields remain the κ-calibrated moments, as before).

    Parameters
    ----------
    se_pre_kappa : the ForecastPoint.se_total *before* the κ override was
        applied — the same SE used when computing the non-conformity scores
        during calibration.
    """
    if calibration is None or fp is None or se_pre_kappa <= 0:
        return fp

    conf_records = calibration.get("conformal_quantile_by_stratum")
    if not conf_records:
        return fp

    # Build / reuse lookup; cache on the calibration dict to avoid rebuilding.
    _cache_key = "_conformal_lookup_cache"
    lookup = calibration.get(_cache_key)
    if lookup is None:
        from .conformal import ConformalRecord, build_conformal_lookup
        records = [
            ConformalRecord(
                indicator=d["indicator"], method=d["method"],
                pop_bucket=d["pop_bucket"], h_bucket=d["h_bucket"],
                quantile=d["quantile"], n_calibration=d["n_calibration"],
                target_coverage=d["target_coverage"],
            )
            for d in conf_records
        ]
        lookup = build_conformal_lookup(records)
        try:
            calibration[_cache_key] = lookup  # type: ignore[index]
        except TypeError:
            pass  # calibration is frozen

    q = lookup(indicator, method_key, pop_bucket, h_bucket)
    if q is None or not math.isfinite(q) or q <= 0:
        return fp

    conformal_half = q * se_pre_kappa
    kappa_half = (fp.ci90_high - fp.ci90_low) / 2.0

    if math.isclose(conformal_half, kappa_half, rel_tol=1e-12):
        return fp  # identical width; nothing to replace

    ci_lo = fp.point - conformal_half
    ci_hi = fp.point + conformal_half
    notes = f"{fp.notes}; conformal_ci(q={q:.3f})" if fp.notes else f"conformal_ci(q={q:.3f})"
    return ForecastPoint(
        point=fp.point,
        se_total=fp.se_total,
        se_sample=fp.se_sample,
        se_forecast=fp.se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method=fp.method,
        target_year=fp.target_year,
        geoid=fp.geoid,
        indicator=fp.indicator,
        horizon=fp.horizon,
        notes=notes,
    )


def _calibrated_macro_weight(
    indicator: str,
    calibration: dict | None,
    fallback: float = 0.30,
    floor: float = 0.05,
    ceiling: float = 0.80,
) -> float:
    """Return the macro/(macro+trend) blend weight for an indicator.

    From Bates-Granger optimal combination of two unbiased estimators:
        w_macro* = RMSE_trend² / (RMSE_trend² + RMSE_macro²)

    `calibration` is the dict written by `scripts/calibrate_anchors.py`;
    if absent or missing this indicator we fall back to `fallback` so
    legacy tests still pass. Bound the result to [floor, ceiling] so a
    single anomalous calibration run can't degenerate the blend (a
    1.0 weight would discard the trend ensemble entirely).
    """
    if calibration is None:
        return fallback
    rmse_table = calibration.get("rmse_by_indicator_method") or {}
    rmse_trend = rmse_table.get(indicator, {}).get("trend_ensemble")
    rmse_anchor = rmse_table.get(indicator, {}).get("multi_anchor")
    if rmse_trend is None or rmse_anchor is None:
        return fallback
    if rmse_trend <= 0 or rmse_anchor <= 0:
        return fallback
    w = (rmse_trend ** 2) / (rmse_trend ** 2 + rmse_anchor ** 2)
    return max(floor, min(ceiling, w))


def _resolve_pop_bucket(
    geoid: str,
    populations: dict[str, int] | None,
) -> str | None:
    """Look up a county's 2020 population bucket.

    Population dict can be passed in explicitly, or this function will
    lazily load the bundled `county_population_2020.json` if available.
    Returns None when the county has no known population (the v3 lookup
    chain treats this as "skip exact, go to global marginalised").
    """
    from .strata import classify_pop
    if populations is None:
        try:
            from ..scripts.load_calibration_panel import load_populations as _lp
            populations = _lp()
        except Exception:  # PanelMissingError or any I/O error
            return None
    pop = populations.get(geoid) if populations else None
    return classify_pop(pop)


def _combine_two_with_rho(
    a: ForecastPoint,
    b: ForecastPoint,
    target_year: int,
    method_label: str,
    rho: float,
) -> ForecastPoint | None:
    """Inverse-variance combine two ForecastPoints at a given correlation.

    Generalises ``combine_forecasts`` for the (trend_ensemble, ml_trend)
    pair, where the appropriate ρ is the inner-correlation (default 0.7
    since both consume the same series). Used internally by
    ``project_ensemble_multi`` when ``use_ml=True``.
    """
    if a.geoid != b.geoid or a.indicator != b.indicator:
        return None
    inv_a = 1.0 / (a.se_total ** 2) if (math.isfinite(a.se_total) and a.se_total > 0) else 0.0
    inv_b = 1.0 / (b.se_total ** 2) if (math.isfinite(b.se_total) and b.se_total > 0) else 0.0
    total = inv_a + inv_b
    if total <= 0:
        # Degenerate: equal weights.
        wa, wb = 0.5, 0.5
    else:
        wa, wb = inv_a / total, inv_b / total
    point = wa * a.point + wb * b.point
    var = (
        (wa * a.se_total) ** 2
        + (wb * b.se_total) ** 2
        + 2.0 * rho * wa * wb * a.se_total * b.se_total
    )
    se_total = math.sqrt(var) if var > 0 else 0.0
    se_sample = math.sqrt((wa * a.se_sample) ** 2 + (wb * b.se_sample) ** 2)
    se_forecast = math.sqrt((wa * a.se_forecast) ** 2 + (wb * b.se_forecast) ** 2)
    ci_lo, ci_hi = ci_from_se(point, se_total)
    notes = (
        f"{method_label}({a.method}={wa:.2f}, {b.method}={wb:.2f}, rho={rho:.2f}); "
        f"{a.method}: {a.notes or ''}; {b.method}: {b.notes or ''}"
    )
    return ForecastPoint(
        point=point,
        se_total=se_total,
        se_sample=se_sample,
        se_forecast=se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method=method_label,
        target_year=target_year,
        geoid=a.geoid,
        indicator=a.indicator,
        horizon=max(a.horizon, b.horizon),
        notes=notes,
    )


def project_ensemble_multi(
    series_observations: Sequence[AcsObservation],
    target_year: int,
    end_year: int | None = None,
    calibration: dict | None = None,
    correlation_rho_inner: float = 0.7,
    correlation_rho_anchor: float = 0.5,
    populations: dict[str, int] | None = None,
    use_ml: bool = True,
    ml_series_by_key: dict[tuple[str, str], Sequence[AcsObservation]] | None = None,
    ml_populations: dict[str, int] | None = None,
    ml_model_cache: dict | None = None,
    ml_panel=None,
    use_kalman: "bool | None" = None,
) -> "ForecastPoint | None":
    """Multi-source anchor ensemble.

    Pulls in CPI / PCE / QCEW / HUD FMR / FHFA HPI (whichever apply
    to the indicator) via `anchors.combined_anchor_rate`, projects
    `latest` forward at the multi-source rate, and combines with the
    trend ensemble at the calibrated optimal weight.

    v3 calibration support
    ----------------------
    When the calibration payload is schema_version=3 (carries
    `strata_records`), each component (trend_ensemble, multi_anchor) is
    individually:

    1. **Bias-corrected** via the geometric multiplier
       `point' = point / exp(b)` looked up by
       (indicator, method, pop_bucket, h_bucket) in the v3 bias records.
    2. **SE-rescaled** via the per-cell κ from the v3 SE records.

    Order matters: bias correction is applied first, then SE override.
    The SE override was calibrated on bias-corrected residuals; reversing
    the order would double-count the level miscalibration in the CI.

    Components are corrected *pre-blend* because the bias and κ records
    are keyed by component method (`trend_ensemble`, `multi_anchor`),
    not by the blended `ensemble_multi_anchor` label.

    Parameters
    ----------
    end_year : int, optional
        The "as-of" year for visibility into anchor sources. Defaults
        to the effective_year of the latest ACS observation. Setting
        this lower simulates a back-test in which only data visible
        on or before `end_year` is used.
    calibration : dict, optional
        Pre-computed calibration payload from
        `scripts/calibrate_anchors.py`. Determines per-source anchor
        weights, macro/trend blend weight, SE inflators, and (v3 only)
        bias corrections. If absent, falls back to equal anchor weights
        and the legacy 0.30 macro weight.
    populations : dict[str, int], optional
        County 2020 populations (geoid → pop). Used to classify the
        target county into a population bucket for v3 stratified
        lookups. If None, the bundled
        `data/calibration_panel/county_population_2020.json` is loaded
        lazily if available; otherwise pop_bucket is None and the v3
        lookup chain falls through to globally marginalised cells.
    use_ml : bool, default True
        Include the cross-county ``ml_trend`` member in the ensemble.
        Default flipped False → True on 2026-08-21: the ship-gate
        ablation passed all three rules (13/16 indicators improve RMSE
        ≥5%, blended CI90 coverage in [85%, 95%] on all 16, zero
        regressions) and the ρ_inner sweep put all 16 indicators in
        band at the production ρ=0.7 — the flip condition documented in
        METHODOLOGY §"Why opt-in". See
        backtests/results/ml_default_ablation_2026-08-21.md.
        When ``ml_series_by_key``/``ml_populations`` are not supplied,
        the bundled calibration panel is lazily loaded (once per
        process, with a shared model cache) — the same training set the
        shipped ml_trend calibration records were fit on; expect a
        several-second first-call cost per (indicator, cutoff). Falls
        back gracefully (ml_fp = None) when the panel is unavailable or
        sklearn is not importable.
    ml_series_by_key, ml_populations : the calibration panel + 2020
        population lookup needed by the HGB trainer. Pass the same dicts
        across many forecasts so the model cache (below) is effective.
    ml_model_cache : dict, optional
        Mutable cache keyed by (indicator, cutoff_year) → TrainedMlModel.
        Pass the same dict across many forecasts at the same cutoff to
        amortise training cost (~2s per indicator × 16 indicators).
    ml_panel : PanelIndex, optional
        Pre-built panel index (output of ``ml_features.build_panel_index``).
        Pass it explicitly when projecting many counties at the same
        cutoff to avoid rebuilding it for each forecast.
    use_kalman : bool or None, default None
        ``None`` (default): the Kalman state-space member replaces the
        Bates-Granger blend only for indicators in
        ``KALMAN_PROMOTED_INDICATORS`` — the per-indicator allowlist
        whose members passed both Kalman ship gates (RMSE ≥5% better
        than the best baseline AND raw CI90 coverage in [85%, 95%];
        2026-08-21 re-verification in
        backtests/results/phase_d_kalman_ablation.md). ``True`` forces
        the Kalman path for any indicator (experiments/ablations);
        ``False`` disables it unconditionally. The wholesale Kalman
        verdict remains HOLD — promotion is strictly per-indicator.
    """
    # Local import to avoid a circular dependency between ensemble and anchors.
    from .anchors import (
        combined_anchor_rate, anchor_as_forecast, load_calibration,
        combined_level_anchor, level_anchor_as_forecast, METHOD_LEVEL_ANCHOR,
    )
    from .strata import classify_horizon

    if not series_observations:
        return None
    indicator = series_observations[-1].indicator
    geoid = series_observations[-1].geoid
    if end_year is None:
        end_year = int(round(effective_year(series_observations[-1])))
    if calibration is None:
        calibration = load_calibration()

    # v3 strata classifications. Both may be None — the lookup chain
    # handles that gracefully by skipping to the global marginalisation.
    horizon_years = target_year - end_year
    h_bucket = classify_horizon(horizon_years) if horizon_years > 0 else None
    pop_bucket = _resolve_pop_bucket(geoid, populations)

    # Kalman state-space path — replaces the Bates-Granger blend when
    # requested (True), never runs when False, and with the default None
    # runs only for the per-indicator allowlist (KALMAN_PROMOTED_INDICATORS,
    # the indicators that passed both Kalman ship gates — see the constant's
    # comment and backtests/results/phase_d_kalman_ablation.md).
    if use_kalman is None:
        use_kalman = indicator in KALMAN_PROMOTED_INDICATORS
    if use_kalman:
        try:
            from ..kalman import project_kalman, METHOD_NAME as _KAL_METHOD
            kal_fp = project_kalman(
                series_observations=series_observations,
                target_year=target_year,
                end_year=end_year,
                calibration=calibration,
                geoid=geoid,
            )
        except Exception:  # pragma: no cover
            kal_fp = None
        if kal_fp is not None:
            kal_fp = _apply_bias_correction(
                kal_fp, indicator, _KAL_METHOD, calibration,
                pop_bucket=pop_bucket, h_bucket=h_bucket,
            )
            _se_pre_kappa_kal = kal_fp.se_total
            kal_fp = _apply_se_override(
                kal_fp, indicator, _KAL_METHOD, calibration,
                pop_bucket=pop_bucket, h_bucket=h_bucket,
            )
            kal_fp = _apply_conformal_interval(
                kal_fp, indicator, _KAL_METHOD, calibration,
                _se_pre_kappa_kal, pop_bucket=pop_bucket, h_bucket=h_bucket,
            )
            return kal_fp

    # Trend ensemble first.
    phi = _lookup_phi(calibration, indicator, "trend_ensemble", pop_bucket, h_bucket)
    components: list[ForecastPoint] = []
    f_damped = project_damped_trend(series_observations, target_year, phi=phi)
    if f_damped is not None:
        components.append(f_damped)
    f_ar1 = project_ar1_log_diff(series_observations, target_year)
    if f_ar1 is not None:
        components.append(f_ar1)

    inner = (
        combine_forecasts(components, target_year, method_label="trend_ensemble")
        if components else None
    )
    if inner is not None:
        # v3: bias correction first, then SE override (correct order)
        inner = _apply_bias_correction(
            inner, indicator, "trend_ensemble", calibration,
            pop_bucket=pop_bucket, h_bucket=h_bucket,
        )
        _se_pre_kappa_inner = inner.se_total
        inner = _apply_se_override(
            inner, indicator, "trend_ensemble", calibration,
            pop_bucket=pop_bucket, h_bucket=h_bucket,
        )
        inner = _apply_conformal_interval(
            inner, indicator, "trend_ensemble", calibration,
            _se_pre_kappa_inner, pop_bucket=pop_bucket, h_bucket=h_bucket,
        )

    # ML trend (third member). Optional — controlled by `use_ml`. We build
    # a cross-county HGB forecast and combine it with the classical trend
    # ensemble at correlation `correlation_rho_inner` (both are
    # target-side models that share the same (geoid, indicator) input
    # series, hence high correlation).
    ml_fp: ForecastPoint | None = None
    if use_ml and (ml_series_by_key is None or ml_populations is None):
        # Caller asked for ML without supplying a panel: fall back to the
        # bundled calibration panel (the training set the shipped ml_trend
        # calibration records were fit on), with a shared module-level
        # model cache so repeated calls amortise HGB training.
        _bundled = _load_bundled_ml_panel()
        if _bundled is not None:
            if ml_series_by_key is None:
                ml_series_by_key = _bundled["series"]
                if ml_panel is None:
                    ml_panel = _bundled["panel"]
            if ml_populations is None:
                ml_populations = _bundled["populations"]
            if ml_model_cache is None:
                ml_model_cache = _ML_DEFAULT_MODEL_CACHE
    if use_ml and ml_series_by_key is not None and ml_populations is not None:
        try:
            from .ml_trend import project_ml_trend_one_shot, METHOD_NAME as _ML_METHOD
            ml_fp = project_ml_trend_one_shot(
                series_by_key=ml_series_by_key,
                populations=ml_populations,
                series_observations=series_observations,
                target_year=target_year,
                cutoff_year=end_year,
                model_cache=ml_model_cache,
                panel=ml_panel,
            )
        except Exception:  # pragma: no cover — graceful degradation
            ml_fp = None
        if ml_fp is not None:
            ml_fp = _apply_bias_correction(
                ml_fp, indicator, _ML_METHOD, calibration,
                pop_bucket=pop_bucket, h_bucket=h_bucket,
            )
            _se_pre_kappa_ml = ml_fp.se_total
            ml_fp = _apply_se_override(
                ml_fp, indicator, _ML_METHOD, calibration,
                pop_bucket=pop_bucket, h_bucket=h_bucket,
            )
            ml_fp = _apply_conformal_interval(
                ml_fp, indicator, _ML_METHOD, calibration,
                _se_pre_kappa_ml, pop_bucket=pop_bucket, h_bucket=h_bucket,
            )

    # If we have both classical trend AND ML, fuse them into a single
    # target-side ensemble before the macro blend. Inverse-variance
    # combination with ρ=correlation_rho_inner (default 0.7) since both
    # consume the same underlying ACS series.
    if inner is not None and ml_fp is not None:
        target_inner = _combine_two_with_rho(
            inner, ml_fp, target_year,
            method_label="target_ensemble",
            rho=correlation_rho_inner,
        )
        if target_inner is not None:
            inner = target_inner
    elif ml_fp is not None and inner is None:
        # No classical trend (e.g. <2 obs), use ML alone as the inner.
        inner = ml_fp

    # Multi-source macro anchor. `combined_anchor_rate` expects the
    # `rmse_by_indicator_source` slice — pass it explicitly so the full
    # calibration dict can be threaded through here without confusion.
    per_source_calib = (calibration or {}).get("rmse_by_indicator_source")
    anchor_rate = combined_anchor_rate(
        indicator=indicator,
        end_year=end_year,
        calibration=per_source_calib,
        calibration_horizon=max(int(round(horizon_years)), 1),
        geoid=geoid,
    )
    anchor_fp: ForecastPoint | None = None
    if anchor_rate is not None:
        anchor_fp = anchor_as_forecast(
            latest=series_observations[-1],
            target_year=target_year,
            anchor_rate=anchor_rate,
        )
        # Same order: bias first, then SE override, then conformal floor.
        anchor_fp = _apply_bias_correction(
            anchor_fp, indicator, "multi_anchor", calibration,
            pop_bucket=pop_bucket, h_bucket=h_bucket,
        )
        _se_pre_kappa_anchor = anchor_fp.se_total
        anchor_fp = _apply_se_override(
            anchor_fp, indicator, "multi_anchor", calibration,
            pop_bucket=pop_bucket, h_bucket=h_bucket,
        )
        anchor_fp = _apply_conformal_interval(
            anchor_fp, indicator, "multi_anchor", calibration,
            _se_pre_kappa_anchor, pop_bucket=pop_bucket, h_bucket=h_bucket,
        )

    # Level anchor — for rate/percentage indicators (unemployment, poverty)
    # where the external series level IS the forecast rather than a growth
    # rate modifier. Bypasses the log-growth-rate arithmetic that breaks on
    # COVID-era swings.
    #
    # Gate: only participate in the blend when the level anchor's calibrated
    # RMSE is within `_LEVEL_ANCHOR_RMSE_RATIO` of the best other method's
    # RMSE. This prevents a poor-fitting level anchor (e.g. LAUS → S2301
    # which has 52% RMSE vs 33% for ml_trend) from dragging the ensemble down
    # even though it gets down-weighted by inverse-variance.
    _LEVEL_ANCHOR_RMSE_RATIO = 1.25
    _rmse_table = (calibration or {}).get("rmse_by_indicator_method", {}).get(indicator, {})
    _level_rmse = _rmse_table.get(METHOD_LEVEL_ANCHOR)
    _best_other_rmse = min(
        (_rmse_table.get(m, float("inf")) for m in ("trend_ensemble", "ml_trend", "multi_anchor")),
        default=float("inf"),
    )
    _level_anchor_ok = (
        _level_rmse is None  # no calibration yet → allow, calibration will judge later
        or math.isfinite(_best_other_rmse) is False  # no other method → allow
        or _level_rmse <= _best_other_rmse * _LEVEL_ANCHOR_RMSE_RATIO
    )

    level_anchor_fp: "ForecastPoint | None" = None
    if _level_anchor_ok:
        try:
            from .sources import available_sources as _avail_sources
            _level_sources = [s for s in _avail_sources(indicator) if s.anchor_type == "level"]
            if _level_sources:
                _level_anchor = combined_level_anchor(
                    indicator=indicator,
                    end_year=end_year,
                    geoid=geoid,
                    sources=_level_sources,
                    calibration=per_source_calib,
                )
                if _level_anchor is not None:
                    level_anchor_fp = level_anchor_as_forecast(
                        latest=series_observations[-1],
                        target_year=target_year,
                        level_anchor=_level_anchor,
                    )
        except Exception:  # pragma: no cover — graceful degradation
            level_anchor_fp = None

    if level_anchor_fp is not None:
        level_anchor_fp = _apply_bias_correction(
            level_anchor_fp, indicator, METHOD_LEVEL_ANCHOR, calibration,
            pop_bucket=pop_bucket, h_bucket=h_bucket,
        )
        _se_pre_kappa_level = level_anchor_fp.se_total
        level_anchor_fp = _apply_se_override(
            level_anchor_fp, indicator, METHOD_LEVEL_ANCHOR, calibration,
            pop_bucket=pop_bucket, h_bucket=h_bucket,
        )
        level_anchor_fp = _apply_conformal_interval(
            level_anchor_fp, indicator, METHOD_LEVEL_ANCHOR, calibration,
            _se_pre_kappa_level, pop_bucket=pop_bucket, h_bucket=h_bucket,
        )

    if inner is None and anchor_fp is None and level_anchor_fp is None:
        return None
    if inner is None and anchor_fp is None:
        return level_anchor_fp

    # Build the rate-anchor blend (inner + anchor_fp) first.
    if inner is None:
        rate_blend: "ForecastPoint | None" = anchor_fp
    elif anchor_fp is None:
        rate_blend = inner
    else:
        macro_weight = _calibrated_macro_weight(indicator, calibration)
        w = [1 - macro_weight, macro_weight]
        pieces = [inner, anchor_fp]
        point = sum(w[i] * pieces[i].point for i in range(2))
        rho = correlation_rho_anchor
        var = 0.0
        for i in range(2):
            for j in range(2):
                corr = 1.0 if i == j else rho
                var += w[i] * w[j] * pieces[i].se_total * pieces[j].se_total * corr
        se_total = math.sqrt(var) if var > 0 else 0.0
        se_sample = math.sqrt(sum((w[i] * pieces[i].se_sample) ** 2 for i in range(2)))
        se_forecast = math.sqrt(sum((w[i] * pieces[i].se_forecast) ** 2 for i in range(2)))
        ci_lo, ci_hi = ci_from_se(point, se_total)
        notes = (
            f"multi_anchor_blend(macro={macro_weight:.2f}, trend={1 - macro_weight:.2f}); "
            f"trend: {inner.notes}; anchor: {anchor_fp.notes}"
        )
        rate_blend = ForecastPoint(
            point=point,
            se_total=se_total,
            se_sample=se_sample,
            se_forecast=se_forecast,
            ci90_low=ci_lo,
            ci90_high=ci_hi,
            method="ensemble_multi_anchor",
            target_year=target_year,
            geoid=inner.geoid,
            indicator=inner.indicator,
            horizon=inner.horizon,
            notes=notes,
        )

    # If a level anchor is available, fuse it with the rate-blend result using
    # inverse-variance combination at ρ=0.5 (lower correlation than within the
    # classical trend ensemble since level and rate anchors use independent data).
    if level_anchor_fp is None:
        return rate_blend

    assert rate_blend is not None
    final = _combine_two_with_rho(
        rate_blend, level_anchor_fp,
        target_year=target_year,
        method_label="ensemble_multi_anchor",
        rho=0.5,
    )
    return final if final is not None else rate_blend
