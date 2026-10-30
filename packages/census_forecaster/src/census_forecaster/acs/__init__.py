"""ACS (American Community Survey) forecasting subpackage.

Modules
-------
* `client` — public-API ACS fetcher with on-disk JSON cache.
* `projection` — damped local linear trend (log space) and AR(1) on
  log-differences. Both produce `ForecastPoint` outputs with sample SE
  (from MOE) and forecast SE (from residual variance) propagated.
* `ensemble` — inverse-variance combination of trend + AR(1), with
  optional macro anchor (BLS-derived growth rate) at fixed weight, plus
  the multi-source `project_ensemble_multi` flavour.
* `anchors` — multi-source macro-anchor combiner (CPI / PCE / QCEW /
  HUD FMR / FHFA HPI) with publication-lag handling.
* `sources` — embedded historical anchor series with documented
  limitations and publication-lag windows.
* `calibration` — hidden-data hold-out calibration of per-(indicator,
  method) SE inflators and per-source weights.
"""
from .anchors import (
    AnchorRate,
    anchor_as_forecast,
    combined_anchor_rate,
    load_calibration,
)
from .calibration import (
    DEFAULT_BIAS_CLAMP_LOG,
    FoldResidual,
    HoldOutFold,
    run_holdout_calibration,
    run_stratified_calibration,
    write_calibration,
)
from .client import DEFAULT_CACHE_PATH, SUSPENDED_ONE_YEAR, AcsClient
from .ensemble import (
    combine_forecasts,
    macro_anchor_projection,
    project_ensemble,
    project_ensemble_multi,
)
from .ml_features import (
    FeatureSpec,
    PanelIndex,
    TrainingMatrix,
    build_panel_index,
    make_feature_spec,
    make_inference_row,
    make_training_rows,
)
from .ml_trend import (
    METHOD_NAME as ML_METHOD_NAME,
)
from .ml_trend import (
    TrainedMlModel,
    project_ml_trend,
    project_ml_trend_one_shot,
    train_ml_model,
)
from .projection import (
    ANNUAL_RATE_CAP,
    EMPIRICAL_SE_INFLATOR,
    AR1LogDiffFit,
    DampedTrendFit,
    effective_year,
    fit_ar1_log_diff,
    fit_damped_trend,
    project_ar1_log_diff,
    project_damped_trend,
)
from .sources import (
    AnchorSource,
    AnnualRate,
    available_sources,
)
from .strata import (
    DEFAULT_N_THRESHOLD,
    WILDCARD,
    HBucket,
    PopBucket,
    StrataRecord,
    classify_horizon,
    classify_pop,
    index_records,
    record_from_dict,
    record_to_dict,
)

__all__ = [
    # Client
    "AcsClient",
    "DEFAULT_CACHE_PATH",
    "SUSPENDED_ONE_YEAR",
    # Projection
    "project_damped_trend",
    "project_ar1_log_diff",
    "fit_damped_trend",
    "fit_ar1_log_diff",
    "DampedTrendFit",
    "AR1LogDiffFit",
    "effective_year",
    "ANNUAL_RATE_CAP",
    "EMPIRICAL_SE_INFLATOR",
    # Ensemble
    "project_ensemble",
    "project_ensemble_multi",
    "macro_anchor_projection",
    "combine_forecasts",
    # Multi-anchor
    "AnchorRate",
    "combined_anchor_rate",
    "anchor_as_forecast",
    "load_calibration",
    # Sources
    "AnchorSource",
    "AnnualRate",
    "available_sources",
    # Calibration
    "HoldOutFold",
    "FoldResidual",
    "run_holdout_calibration",
    "run_stratified_calibration",
    "write_calibration",
    "DEFAULT_BIAS_CLAMP_LOG",
    # Strata (v3)
    "PopBucket",
    "HBucket",
    "StrataRecord",
    "DEFAULT_N_THRESHOLD",
    "classify_pop",
    "classify_horizon",
    "index_records",
    "record_to_dict",
    "record_from_dict",
    "WILDCARD",
    # ML trend (third ensemble member)
    "PanelIndex",
    "FeatureSpec",
    "TrainingMatrix",
    "build_panel_index",
    "make_feature_spec",
    "make_training_rows",
    "make_inference_row",
    "ML_METHOD_NAME",
    "TrainedMlModel",
    "train_ml_model",
    "project_ml_trend",
    "project_ml_trend_one_shot",
]
