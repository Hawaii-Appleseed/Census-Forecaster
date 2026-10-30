"""BLS (Bureau of Labor Statistics) forecasting subpackage.

Modules
-------
* `client` — public-API BLS fetcher + period helpers (find_nearest_periods,
  expected_latest_period_bimonthly, fetch_if_stale, ...).
* `projection` — recency-weighted, damped, capped CPI forward-projection
  with closed-form 90% prediction intervals.
* `calibration` — v3 stratified calibration generator. Stratifies κ and
  geometric bias by `(series_id, h_bucket, vol_regime)` over a multi-MSA
  panel. Output JSON drives the v3 lookup in `compute_cpi_ratio`.
* `panel` — multi-MSA panel definition (top 10 MSAs + national + Honolulu
  × 5 CPI series each).

When the bundled `data/anchors/bls_calibration.json` is present at
import time, `compute_cpi_ratio(... calibration=load_bls_calibration())`
returns ratios with stratified κ and bias correction applied; otherwise
it falls back to the legacy global `_PROJ_SE_INFLATOR=1.50` for
backward compatibility.
"""
from .calibration import (
    DEFAULT_BIAS_CLAMP_LOG,
    H_BUCKETS_BLS,
    VOL_REGIMES,
    BlsFoldResidual,
    h_bucket_for_months,
    load_bls_calibration,
    lookup_bls_strata,
    resolve_kappa_and_bias,
    run_stratified_bls_calibration,
    vol_regime_for_value,
    write_bls_calibration,
)
from .client import (
    BLS_API_URL,
    BLS_RELEASE_DAY,
    HONOLULU_DATA_MONTHS,
    cache_has_period,
    date_to_bls_period,
    expected_latest_period_bimonthly,
    fetch_and_cache,
    fetch_cpi_data,
    fetch_if_stale,
    find_nearest_periods,
    get_latest,
    get_value,
    load_cached_cpi,
)
from .panel import (
    BLS_PANEL_AREAS,
    BLS_PANEL_SERIES,
    BlsArea,
    BlsSeriesKind,
    all_panel_series_ids,
    panel_series_for_area,
    panel_series_for_kind,
    parse_series_id,
)
from .projection import (
    PROJ_DAMPING,
    PROJ_MONTHLY_CAP,
    ProjectionResult,
    compute_cpi_ratio,
    damped_compound_factor,
    forecast_se_log,
    project_forward,
    project_forward_full,
    residual_log_std,
    smoothed_monthly_rate,
)

__all__ = [
    # Client
    "BLS_API_URL",
    "fetch_cpi_data",
    "fetch_and_cache",
    "fetch_if_stale",
    "load_cached_cpi",
    "get_value",
    "get_latest",
    "date_to_bls_period",
    "find_nearest_periods",
    "expected_latest_period_bimonthly",
    "cache_has_period",
    "HONOLULU_DATA_MONTHS",
    "BLS_RELEASE_DAY",
    # Projection
    "ProjectionResult",
    "project_forward",
    "project_forward_full",
    "smoothed_monthly_rate",
    "residual_log_std",
    "forecast_se_log",
    "damped_compound_factor",
    "compute_cpi_ratio",
    "PROJ_DAMPING",
    "PROJ_MONTHLY_CAP",
    # Calibration (v3)
    "BlsFoldResidual",
    "DEFAULT_BIAS_CLAMP_LOG",
    "H_BUCKETS_BLS",
    "VOL_REGIMES",
    "h_bucket_for_months",
    "vol_regime_for_value",
    "run_stratified_bls_calibration",
    "write_bls_calibration",
    "load_bls_calibration",
    "lookup_bls_strata",
    "resolve_kappa_and_bias",
    # Panel
    "BlsArea",
    "BlsSeriesKind",
    "BLS_PANEL_AREAS",
    "BLS_PANEL_SERIES",
    "all_panel_series_ids",
    "panel_series_for_area",
    "panel_series_for_kind",
    "parse_series_id",
]
