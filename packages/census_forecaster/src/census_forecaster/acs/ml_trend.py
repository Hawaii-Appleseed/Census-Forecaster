"""Cross-county histogram-boosting trend forecaster.

This module is the third member of the ACS forecast ensemble. The first
two — ``project_damped_trend`` and ``project_ar1_log_diff`` in
``projection.py`` — operate on a single (geoid, indicator) series in
isolation and so cannot exploit cross-county or cross-indicator signal.
The model here pools all 147 counties × 16 indicators into a single
training panel and lets a histogram-based gradient-boosting tree learn
patterns the classical models can't see.

Why histogram boosting (and not LightGBM directly)
--------------------------------------------------
sklearn's ``HistGradientBoostingRegressor`` is the same algorithmic family
as LightGBM (histogram-binned gradient boosting with split finding on
discretised feature values). It is competitive on tabular tasks at the
sample size we have here (~5–10k training rows per indicator) while
shipping inside the existing sklearn install — no libomp/OpenMP runtime
dependency, no extra wheel to vendor, no platform-specific build issues.
LightGBM-via-pip on macOS requires a separate libomp install, which is a
fragile assumption for a research package.

Walk-forward discipline
-----------------------
The model is keyed by (target_indicator, cutoff_year). At calibration
time every fold (anchor=Y, h=h) trains a fresh model with
``cutoff_year = Y`` so the training set sees only data with target
year ≤ Y. The trained models are cached so a single calibration pass
reuses them across (geoid, h) folds at the same anchor. At production
time ``cutoff_year`` is the most recent observed year and the model
trains once on everything available.

SE estimation
-------------
The model's residuals are computed in log-growth space (the target the
tree fits). Residual variance is estimated per-horizon-bucket on the
training residuals and converted to dollar-space SE via the delta method
``σ_y ≈ σ_logy × y``, the same convention used by the classical models.
The ``EMPIRICAL_SE_INFLATOR`` is applied so the resulting CI matches the
diagnostic conventions of the existing ensemble; the v3 stratified κ
calibration then refines it per (indicator, pop_bucket, h_bucket) just
like for the trend / anchor methods.
"""
from __future__ import annotations

import math
import sys
from dataclasses import dataclass, field
from typing import Mapping, Optional, Sequence

from common.models import AcsObservation, ForecastPoint
from common.moe import moe_to_se, combine_se, ci_from_se
from .projection import (
    ANNUAL_RATE_CAP,
    EMPIRICAL_SE_INFLATOR,
    effective_year,
)
from .ml_features import (
    FeatureSpec,
    PanelIndex,
    TrainingMatrix,
    build_panel_index,
    load_county_data,
    load_market_signals_data,
    load_national_macro_data,
    load_state_data,
    make_feature_spec,
    make_inference_row,
    make_training_rows,
)


# Method label used throughout the codebase (calibration strata, ensemble
# notes, etc.). Kept separate from "lgbm_trend" in the original plan to
# avoid suggesting we ship LightGBM bytecode — see module docstring for
# the rationale on choosing sklearn HGB.
METHOD_NAME = "ml_trend"

# Minimum training rows below which we refuse to fit a model and let the
# ensemble fall back to the classical members. 200 is conservative for
# ~12-feature tree boosting; below this the residual-variance estimate
# itself is unreliable.
_MIN_TRAINING_ROWS = 200

# HistGradientBoosting hyperparameters. Conservative on regularisation —
# these are NOT tuned per indicator; doing so on this sample size is
# overfitting to the back-test. See METHODOLOGY.md after the ship gate.
_HGB_PARAMS: dict = {
    "max_iter": 300,
    "learning_rate": 0.05,
    "max_depth": 6,
    "max_leaf_nodes": 15,
    "min_samples_leaf": 20,
    "l2_regularization": 0.1,
    "early_stopping": True,
    "validation_fraction": 0.15,
    "n_iter_no_change": 25,
    "random_state": 42,
}


# -----------------------------------------------------------------------------
# Trained model holder
# -----------------------------------------------------------------------------

@dataclass
class TrainedMlModel:
    """An indicator+cutoff-specific HGB model + per-horizon residual std.

    Held in a cache keyed by (indicator, cutoff_year) so the calibration
    pass and the ensemble combiner can reuse the same fit. The dataclass
    is intentionally lightweight — the heavy data is the sklearn estimator
    object.
    """
    indicator: str
    cutoff_year: int
    estimator: object  # HistGradientBoostingRegressor
    spec: FeatureSpec
    residual_std_by_horizon: dict[int, float]  # h → std of in-sample log-growth residuals
    residual_std_global: float  # fallback for horizons we didn't train on
    n_train: int
    feature_names: tuple[str, ...] = field(default_factory=tuple)


# -----------------------------------------------------------------------------
# Training
# -----------------------------------------------------------------------------

def _try_import_sklearn():
    """Return (HistGradientBoostingRegressor, np) or None if unavailable."""
    try:
        import numpy as _np
        from sklearn.ensemble import HistGradientBoostingRegressor as _HGB
    except ImportError:
        return None
    return (_HGB, _np)


def train_ml_model(
    series_by_key: Mapping[tuple[str, str], Sequence[AcsObservation]],
    populations: Mapping[str, int],
    indicator: str,
    cutoff_year: int,
    horizons: Sequence[int] = (1, 2, 3, 4, 5),
    panel: Optional[PanelIndex] = None,
    county_data: Optional[Mapping[str, Mapping[str, Mapping[int, float]]]] = None,
    market_data: Optional[Mapping[str, Mapping[int, float]]] = None,
    national_data: Optional[Mapping[str, Mapping[int, float]]] = None,
    state_data: Optional[Mapping[str, Mapping[str, Mapping[int, float]]]] = None,
) -> Optional[TrainedMlModel]:
    """Fit one HGB model for one (indicator, cutoff_year).

    Returns None when (a) sklearn is unavailable, (b) the training matrix
    has fewer than ``_MIN_TRAINING_ROWS`` rows, or (c) sklearn raises
    during fit. In every case the caller falls back to the existing
    classical ensemble.
    """
    deps = _try_import_sklearn()
    if deps is None:
        return None
    HGB, np = deps  # noqa: N806

    if panel is None:
        _cty = county_data if county_data is not None else load_county_data()
        _mkt = market_data if market_data is not None else load_market_signals_data()
        _nm = national_data if national_data is not None else load_national_macro_data()
        _st = state_data if state_data is not None else load_state_data()
        panel = build_panel_index(
            series_by_key,
            county_data=_cty,
            market_data=_mkt,
            national_data=_nm,
            state_data=_st,
        )

    matrix: TrainingMatrix = make_training_rows(
        panel, populations, indicator, cutoff_year, horizons,
    )
    if matrix.spec is None or len(matrix.X) < _MIN_TRAINING_ROWS:
        return None

    # Convert to numpy (HGB requires float arrays; NaN is tolerated for
    # missing features via the native missing-value branch).
    X = np.asarray(matrix.X, dtype=float)
    y = np.asarray(matrix.y, dtype=float)

    try:
        est = HGB(**_HGB_PARAMS)
        est.fit(X, y)
    except Exception as exc:  # pragma: no cover — sklearn fail is a degrade-not-crash
        print(
            f"[ml_trend] HGB fit failed for ({indicator}, cutoff={cutoff_year}): {exc}",
            file=sys.stderr,
        )
        return None

    # Residual std by horizon. The training residuals are in log-growth
    # space; we report them per-h so the inference path can pick the
    # appropriate one.
    preds = est.predict(X)
    residuals = y - preds  # shape (n_train,)
    by_h: dict[int, list[float]] = {}
    for resid, (_g, _a, _t, h) in zip(residuals, matrix.meta):
        by_h.setdefault(int(h), []).append(float(resid))

    std_by_h: dict[int, float] = {}
    all_resid: list[float] = []
    for h, items in by_h.items():
        all_resid.extend(items)
        if len(items) < 2:
            continue
        mean = sum(items) / len(items)
        var = sum((r - mean) ** 2 for r in items) / (len(items) - 1)
        std_by_h[h] = math.sqrt(max(var, 0.0))
    if all_resid:
        mean = sum(all_resid) / len(all_resid)
        var = sum((r - mean) ** 2 for r in all_resid) / max(len(all_resid) - 1, 1)
        std_global = math.sqrt(max(var, 0.0))
    else:
        std_global = 0.0

    return TrainedMlModel(
        indicator=indicator,
        cutoff_year=cutoff_year,
        estimator=est,
        spec=matrix.spec,
        residual_std_by_horizon=std_by_h,
        residual_std_global=std_global,
        n_train=len(matrix.X),
        feature_names=matrix.spec.column_names,
    )


# -----------------------------------------------------------------------------
# Inference (single forecast)
# -----------------------------------------------------------------------------

def _residual_std_for_h(model: TrainedMlModel, horizon: int) -> float:
    """Pick the per-horizon residual std, falling back to global if missing."""
    h = int(round(horizon))
    if h in model.residual_std_by_horizon:
        return model.residual_std_by_horizon[h]
    # For h beyond the trained range, scale the global by sqrt(h/h_max_trained)
    # so longer-horizon CIs widen plausibly. Keeps behaviour sensible even
    # if the back-test happens to pick a horizon the trainer didn't see.
    if model.residual_std_by_horizon:
        h_max = max(model.residual_std_by_horizon.keys())
        anchor_std = model.residual_std_by_horizon[h_max]
        return anchor_std * math.sqrt(max(h, 1) / max(h_max, 1))
    return model.residual_std_global


def project_ml_trend(
    series_observations: Sequence[AcsObservation],
    target_year: int,
    model: TrainedMlModel,
    panel: PanelIndex,
    populations: Mapping[str, int],
) -> Optional[ForecastPoint]:
    """Predict the target year for a single (geoid, indicator) using the trained HGB.

    Mirrors the public surface of ``project_damped_trend`` — takes a list
    of observations (so it can be plugged into the same ensemble pipeline)
    and returns a ``ForecastPoint`` with method=``"ml_trend"`` or ``None``
    on graceful failure.
    """
    if not series_observations:
        return None

    deps = _try_import_sklearn()
    if deps is None:
        return None
    _HGB, np = deps  # noqa: N806

    latest = series_observations[-1]
    if latest.indicator != model.indicator:
        raise ValueError(
            f"project_ml_trend: model is for {model.indicator!r} but "
            f"observations are {latest.indicator!r}"
        )

    latest_eff_year = effective_year(latest)
    horizon = target_year - latest_eff_year
    if horizon <= 0:
        # Pass through latest observation in forecast-shaped form so the
        # ensemble combiner doesn't have to special-case h=0.
        sample_se = moe_to_se(latest.moe)
        ci_lo, ci_hi = ci_from_se(latest.estimate, sample_se)
        return ForecastPoint(
            point=latest.estimate,
            se_total=sample_se if math.isfinite(sample_se) else 0.0,
            se_sample=sample_se if math.isfinite(sample_se) else 0.0,
            se_forecast=0.0,
            ci90_low=ci_lo,
            ci90_high=ci_hi,
            method=METHOD_NAME + "_passthrough",
            target_year=target_year,
            geoid=latest.geoid,
            indicator=latest.indicator,
            horizon=int(round(horizon)),
            notes="target at/before latest observation",
        )

    # Anchor the inference at the latest observation's effective year.
    # ``cutoff_year`` may be > anchor (production case where the model was
    # trained with everything visible); they need not match.
    anchor_year = int(round(latest_eff_year))
    h_int = int(round(horizon))

    row = make_inference_row(
        panel=panel,
        populations=populations,
        geoid=latest.geoid,
        target_indicator=model.indicator,
        anchor_year=anchor_year,
        horizon=h_int,
        spec=model.spec,
    )
    if row is None:
        # Anchor or anchor-1 unobserved — bail; ensemble falls back to classical.
        return None

    X = np.asarray([row], dtype=float)
    log_growth = float(model.estimator.predict(X)[0])

    # Apply the same per-year compound-rate cap that the classical models
    # use, in log space. Keeps a single noisy lag from blowing up multi-h
    # extrapolation.
    implied_annual = math.expm1(log_growth / max(horizon, 1.0))
    notes = ""
    if implied_annual > ANNUAL_RATE_CAP:
        log_growth = horizon * math.log1p(ANNUAL_RATE_CAP)
        notes = f"capped at +{ANNUAL_RATE_CAP * 100:.1f}%/yr momentum ceiling"
    elif implied_annual < -ANNUAL_RATE_CAP:
        log_growth = horizon * math.log1p(-ANNUAL_RATE_CAP)
        notes = f"capped at -{ANNUAL_RATE_CAP * 100:.1f}%/yr momentum floor"

    point = latest.estimate * math.exp(log_growth)

    # Forecast SE in log-growth space → dollar space via delta method.
    # Apply the small-sample correction by using n_train as the effective
    # degrees-of-freedom — the classical models use n/(n-2) but at
    # n_train ≈ thousands the correction is 1.0 to four decimals; keep
    # the math but the effect is negligible.
    resid_std_log = _residual_std_for_h(model, h_int)
    if model.n_train > 2:
        small_sample = model.n_train / (model.n_train - 2)
    else:
        small_sample = 2.0
    se_forecast_log = resid_std_log * math.sqrt(small_sample) * EMPIRICAL_SE_INFLATOR
    se_forecast = se_forecast_log * point

    sample_relative = (
        moe_to_se(latest.moe) / latest.estimate if latest.estimate > 0 else 0.0
    )
    if not math.isfinite(sample_relative):
        sample_relative = 0.0
    se_sample = sample_relative * point

    se_total = combine_se(se_sample, se_forecast)
    ci_lo, ci_hi = ci_from_se(point, se_total)

    note_pieces = [f"ml_trend(n_train={model.n_train}, h_resid_std={resid_std_log:.4f})"]
    if notes:
        note_pieces.append(notes)
    notes_out = "; ".join(note_pieces)

    return ForecastPoint(
        point=point,
        se_total=se_total,
        se_sample=se_sample,
        se_forecast=se_forecast,
        ci90_low=ci_lo,
        ci90_high=ci_hi,
        method=METHOD_NAME,
        target_year=target_year,
        geoid=latest.geoid,
        indicator=latest.indicator,
        horizon=h_int,
        notes=notes_out,
    )


# -----------------------------------------------------------------------------
# High-level convenience: train+predict in one call
# -----------------------------------------------------------------------------

def project_ml_trend_one_shot(
    series_by_key: Mapping[tuple[str, str], Sequence[AcsObservation]],
    populations: Mapping[str, int],
    series_observations: Sequence[AcsObservation],
    target_year: int,
    cutoff_year: Optional[int] = None,
    model_cache: Optional[dict[tuple[str, int], TrainedMlModel]] = None,
    panel: Optional[PanelIndex] = None,
    county_data: Optional[Mapping[str, Mapping[str, Mapping[int, float]]]] = None,
    market_data: Optional[Mapping[str, Mapping[int, float]]] = None,
    national_data: Optional[Mapping[str, Mapping[int, float]]] = None,
    state_data: Optional[Mapping[str, Mapping[str, Mapping[int, float]]]] = None,
) -> Optional[ForecastPoint]:
    """One-shot: train (or fetch cached) model and produce a forecast.

    Used by the ensemble combiner and the back-test harness so callers
    don't have to know the training/inference split.

    Cache semantics: ``model_cache`` is mutated in place; pass the same
    dict across many forecasts to amortise training cost. Pass ``None`` to
    train fresh every time (correctness-equivalent, just slower).
    """
    if not series_observations:
        return None
    indicator = series_observations[-1].indicator

    if panel is None:
        _cty = county_data if county_data is not None else load_county_data()
        _mkt = market_data if market_data is not None else load_market_signals_data()
        _nm = national_data if national_data is not None else load_national_macro_data()
        _st = state_data if state_data is not None else load_state_data()
        panel = build_panel_index(
            series_by_key,
            county_data=_cty,
            market_data=_mkt,
            national_data=_nm,
            state_data=_st,
        )

    if cutoff_year is None:
        cutoff_year = int(round(effective_year(series_observations[-1])))

    cache_key = (indicator, cutoff_year)
    model: Optional[TrainedMlModel] = None
    if model_cache is not None:
        model = model_cache.get(cache_key)
    if model is None:
        model = train_ml_model(
            series_by_key=series_by_key,
            populations=populations,
            indicator=indicator,
            cutoff_year=cutoff_year,
            panel=panel,
        )
        if model is None:
            return None
        if model_cache is not None:
            model_cache[cache_key] = model

    return project_ml_trend(
        series_observations=series_observations,
        target_year=target_year,
        model=model,
        panel=panel,
        populations=populations,
    )


__all__ = [
    "METHOD_NAME",
    "TrainedMlModel",
    "train_ml_model",
    "project_ml_trend",
    "project_ml_trend_one_shot",
]
