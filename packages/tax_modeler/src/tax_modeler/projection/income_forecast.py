"""Census-Forecaster-driven Hawaii / national income growth factors.

Two public entry points:

  - :func:`get_hawaii_real_growth_factor` — Hawaii residents. Uses
    Honolulu B19013 (geoid 15003) deflated by Honolulu CPI All-Items.
  - :func:`get_national_real_growth_factor` — Hawaii nonresidents.
    Inverse-variance-weighted geometric mean of B19013 forecasts across
    a representative basket of large mainland counties (see
    :data:`_NATIONAL_GEOIDS`), deflated by the BEA national PCE chain
    price index.

Both delegate to a shared private helper, :func:`_compute_real_growth_factor`,
which implements the multi-county aggregation + CPI deflation. The
single-county case (Hawaii) is the degenerate aggregation of N=1 county.

Opt-out switch
--------------
Set the environment variable ``TAX_MODELER_USE_ENSEMBLE_GROWTH=0`` (or
``false`` / ``no``) to bypass this module entirely and force callers to
use the hardcoded ``RESIDENT_GROWTH`` / ``NONRESIDENT_GROWTH`` constants.
Useful for diagnosing whether a regression is caused by the ensemble path
or by the underlying tax math. The env var is checked at every call site
so it can be flipped without restarting a long-running process.

Why a single Hawaii proxy (Honolulu, geoid 15003)
--------------------------------------------------
Median household income (B19013) is forecast at the county level. Hawaii
has 4 counties; Honolulu carries 70% of state population and has the
smallest MOE in the panel. For tax_modeler's purpose — applying a
SCALAR growth factor to PUMS-level individual incomes — a state-aggregate
factor is what we need. We use Honolulu as the proxy because:

  - Population-weighted averaging of MEDIANS across counties is
    statistically incorrect (you can't average medians).
  - Population-weighted averaging of GROWTH RATES is defensible (rates
    are approximately log-additive) — but the smallest-MOE county
    dominates the variance-weighted average anyway.
  - For Hawaii, that's Honolulu.

Why an 8-county basket for the national proxy
---------------------------------------------
Hawaii nonresidents earn income from outside Hawaii, so Honolulu B19013 +
Honolulu CPI is the wrong proxy. We use the 8 largest mainland counties
in census_forecaster's bundled panel (LA, Cook, Harris, Maricopa,
San Diego, Orange, Miami-Dade, Dallas — all xlarge population bucket
with full 14-obs B19013 series) deflated by the national PCE deflator.

Aggregation across the basket uses **inverse-variance weighting** on the
log-growth-rate via the delta method
(``var(log(point/base)) ≈ (se_total/point)^2``). This dominates over
population-weighting (which would over-weight LA + Cook + Harris) because
it gives more weight to forecasts with tighter standard errors regardless
of which county they came from. Population weighting is offered as a
sensitivity-analysis fallback (``weighting="population"``).

Why explicit CPI deflation
--------------------------
``project_ensemble_multi`` produces a NOMINAL forecast (e.g., median
income $114,204 in 2026). tax_modeler's ``apply_income_growth`` is
documented as applying REAL growth (purchasing-power-adjusted). To
convert nominal → real we deflate by the price-index ratio
``cpi_target_year / cpi_base_year``. CPI past the bundled 2024 endpoint
is projected forward with :func:`census_forecaster.project_damped_trend`
— the same methodology census_forecaster uses internally for indicator
trends, so income and CPI projections are mutually consistent. The
damped-trend model dampens the COVID-era inflation spike and converges
toward the long-run rate (~2.5%/yr Honolulu CPI, ~2%/yr national PCE),
avoiding the bias that a naive recent-window extrapolation would
introduce.

Note: PCE is one of the input anchors to ``project_ensemble_multi`` for
B19013 *level* calibration, not as a deflator. Explicit deflation of the
nominal forecast by the PCE projection therefore is not double-counting.

Graceful degradation
--------------------
If 1 of N county forecasts in the basket fails (panel missing the geoid,
ensemble returning None), it is logged at WARNING and skipped; the
remaining counties are aggregated. The public wrappers return ``None``
only if **all** counties fail, in which case ``apply_income_growth``
falls back to the hardcoded constant.
"""
from __future__ import annotations

import functools
import json
import logging
import math
import os
from collections.abc import Callable
from importlib.resources import files

logger = logging.getLogger(__name__)


# Hawaii state FIPS = "15"; Honolulu county = "15003".
DEFAULT_HAWAII_GEOID: str = "15003"
DEFAULT_INCOME_INDICATOR: str = "B19013_001E"

# BLS series ID for the preferred CPI path (bundled panel).
# Urban Hawaii all-items (bimonthly, published from 2017): more precise
# than the annual JSON.
#
# CORRECTED 2026-07-27. This was `CUURS49ASA0`, labelled "Urban Honolulu"
# throughout the repo, but the BLS API catalog resolves that ID to
# *Los Angeles-Long Beach-Anaheim, CA*. Los Angeles ran ~0.34 pp/yr hotter
# than Urban Hawaii over 2018-2025 (CAGR 3.687% vs 3.350%), so every
# Hawaii deflator built on it was overstated — ~2.6% compounded across a
# 2023->2031 window. `S49F` is the real Hawaii area code; see
# `census_forecaster.bls.panel` for the full area-label audit.
_HONOLULU_BLS_SERIES_ID: str = "CUURS49FSA0"

# 8 largest mainland US counties (excluding Hawaii / Alaska) by 2020 pop.
# Each has a full 14-obs B19013_001E series in the bundled panel.
_NATIONAL_GEOIDS: tuple[str, ...] = (
    "06037",  # Los Angeles, CA
    "17031",  # Cook, IL
    "48201",  # Harris, TX
    "04013",  # Maricopa, AZ
    "06073",  # San Diego, CA
    "06059",  # Orange, CA
    "12086",  # Miami-Dade, FL
    "48113",  # Dallas, TX
)

# 2020 populations for the national basket (used only when
# ``weighting="population"``; not the default).
_COUNTY_POP_2020: dict[str, int] = {
    "06037": 10_040_682,
    "17031":  5_169_517,
    "48201":  4_680_609,
    "04013":  4_412_779,
    "06073":  3_323_970,
    "06059":  3_170_345,
    "12086":  2_705_528,
    "48113":  2_622_634,
}

# Env var to disable the ensemble path. Truthy values: "1", "true", "yes",
# "on" (case-insensitive). Anything else (including the var being unset)
# leaves the ensemble enabled.
_ENV_VAR = "TAX_MODELER_USE_ENSEMBLE_GROWTH"


def _ensemble_enabled() -> bool:
    """Return False if ``TAX_MODELER_USE_ENSEMBLE_GROWTH`` is set to a
    falsy value, True otherwise."""
    val = os.environ.get(_ENV_VAR)
    if val is None:
        return True  # default: enabled
    return val.strip().lower() in {"1", "true", "yes", "on"}


# -----------------------------------------------------------------------------
# Series + price-index loaders (memoised)
# -----------------------------------------------------------------------------


@functools.lru_cache(maxsize=16)
def _load_b19013_series(geoid: str) -> tuple:
    """Load and cache the B19013 series for ``geoid`` from the bundled panel.

    Returns an empty tuple if the geoid isn't represented in the panel.
    """
    from common.models import AcsObservation

    panel_path = files("census_forecaster") / "data" / "calibration_panel" / "acs_panel.json"
    with panel_path.open() as f:
        panel = json.load(f)

    obs = [
        AcsObservation(
            estimate=o["estimate"],
            moe=o["moe"],
            year=o["year"],
            vintage=o["vintage"],
            geoid=o["geoid"],
            indicator=o["indicator"],
        )
        for o in panel["observations"]
        if o["geoid"] == geoid and o["indicator"] == DEFAULT_INCOME_INDICATOR
    ]
    return tuple(sorted(obs, key=lambda o: o.year))


@functools.lru_cache(maxsize=1)
def _load_cpi_honolulu_series() -> dict[int, float]:
    """Load and cache the Honolulu CPI All-Items series (2010-2024 in bundle)."""
    cpi_path = files("census_forecaster") / "data" / "anchors" / "cpi_honolulu_allitems.json"
    with cpi_path.open() as f:
        d = json.load(f)
    return {int(k): float(v) for k, v in d["values_by_year"].items()}


@functools.lru_cache(maxsize=1)
def _load_bls_panel() -> dict[str, list]:
    """Load the bundled monthly BLS CPI panel (55 series, updated quarterly).

    Returns the inner ``series`` dict: ``{series_id: [{year, period, value}, ...]}``.
    The panel ships inside the census_forecaster wheel and is always available
    without network access. Its latest fetch date determines how far into the
    current year observed data extends (typically within 2-3 months of real-time).
    """
    import json
    from importlib.resources import files
    panel_path = files("census_forecaster") / "data" / "bls_panel" / "cpi_panel.json"
    with panel_path.open() as f:
        d = json.load(f)
    return d.get("series", {})


from dataclasses import dataclass

# Z-score for symmetric 90% intervals (mirrors census_forecaster.bls._Z_90).
_Z_90 = 1.6448536269514722


@dataclass(frozen=True)
class RealGrowthDetail:
    """Real growth factor with its uncertainty decomposition.

    All SEs are in log space. ``se_log_real`` combines the nominal
    (ACS-forecast) and inflation (CPI-projection) legs under an
    independence assumption — different data sources, different models —
    so ``se_log_real = sqrt(se_log_nominal² + se_log_inflation²)``.
    Fields are None when the corresponding leg exposes no SE (e.g. the
    annual-JSON CPI fallback, or the PCE path).
    """
    real_factor: float
    nominal_factor: float
    inflation_factor: float
    se_log_nominal: float | None
    se_log_inflation: float | None
    se_log_real: float | None
    real_ci90_low: float | None
    real_ci90_high: float | None
    inflation_source: str          # 'bls_panel' | 'annual_json' | 'none'
    kappa_used: float | None
    kappa_source: str | None
    horizon_months: int | None


@functools.lru_cache(maxsize=1)
def _load_bls_calibration() -> dict | None:
    """Load and cache the bundled BLS v3 stratified calibration.

    Returns None when the file is absent or unreadable, in which case
    ``compute_cpi_ratio`` falls back to the legacy global κ=1.50 and
    applies no bias correction.
    """
    from census_forecaster.bls.calibration import load_bls_calibration
    return load_bls_calibration()


def _bls_cpi_ratio_detail(
    series_id: str, base_year: int, target_year: int,
) -> dict | None:
    """Dec→Dec CPI ratio with the full v3 diagnostic surface.

    Preferred over the annual-JSON path because:
    - Monthly data extends ~2-3 months past real-time vs annual-JSON's Dec cutoff.
    - ``project_forward_full`` (damped compound) is better calibrated than
      ``project_damped_trend`` on annual data.

    The v3 stratified calibration is passed through (2026-07-30): the
    geometric bias correction lands on ``ratio``, and the κ-rescaled
    log-space SE / CI90 bounds are carried in ``forecast_se`` /
    ``ratio_ci90_low`` / ``ratio_ci90_high`` for callers that propagate
    uncertainty (see :class:`RealGrowthDetail`).

    Returns the ``compute_cpi_ratio`` result dict, or None if the series
    or either date is unavailable.
    """
    from datetime import date

    from census_forecaster.bls.projection import compute_cpi_ratio

    panel = _load_bls_panel()
    if series_id not in panel:
        logger.debug("BLS panel missing series %s", series_id)
        return None

    cpi_data = {series_id: panel[series_id]}
    baseline_date = date(base_year, 12, 1)
    target_date = date(target_year, 12, 1)

    result = compute_cpi_ratio(
        cpi_data, series_id, baseline_date, target_date,
        calibration=_load_bls_calibration(),
    )
    if result["method"] == "unavailable" or not (result["ratio"] > 0):
        logger.warning(
            "compute_cpi_ratio unavailable for %s (%d→%d)", series_id, base_year, target_year
        )
        return None

    logger.debug(
        "BLS panel CPI ratio %s (%d→%d Dec): %.4f [method=%s, horizon=%s mo, "
        "kappa=%s (%s), bias=%s (%s)]",
        series_id, base_year, target_year,
        result["ratio"], result["method"], result["horizon_months"],
        result["kappa_used"], result["kappa_source"],
        result["bias_used"], result["bias_source"],
    )
    return result


def _bls_cpi_ratio(series_id: str, base_year: int, target_year: int) -> float | None:
    """Point-ratio wrapper over :func:`_bls_cpi_ratio_detail`."""
    detail = _bls_cpi_ratio_detail(series_id, base_year, target_year)
    return detail["ratio"] if detail is not None else None


@functools.lru_cache(maxsize=1)
def _load_pce_deflator_series() -> dict[int, float]:
    """Load and cache the BEA national PCE chain price index (2010-2024 in bundle).

    BEA NIPA Table 2.3.4, line 1 (Personal consumption expenditures price index).
    Annual, 2017=100. The national PCE price index is the appropriate deflator
    for nonresident income forecasts (national-level cost of living).
    """
    pce_path = files("census_forecaster") / "data" / "anchors" / "pce_deflator.json"
    with pce_path.open() as f:
        d = json.load(f)
    return {int(k): float(v) for k, v in d["values_by_year"].items()}


# -----------------------------------------------------------------------------
# Damped-trend price-index projection
# -----------------------------------------------------------------------------


def _project_cpi(cpi_by_year: dict[int, float], target_year: int) -> float:
    """Return the price index for ``target_year`` (projecting past observed range).

    Within the observed range, returns the bundled value (or linearly
    interpolates a gap year). Past the latest observation, projects
    forward with :func:`census_forecaster.project_damped_trend` —
    matching the methodology census_forecaster uses for indicator
    trends, so the deflated income figure is internally consistent.

    Parameters
    ----------
    cpi_by_year:
        Year → index value mapping (CPI, PCE, etc. — any annual series).
    target_year:
        Year to return the index for.

    Raises
    ------
    KeyError
        If ``target_year`` < min(observed). No backward extrapolation.
    RuntimeError
        If the damped-trend projection fails for any reason.
    """
    years = sorted(cpi_by_year.keys())
    if target_year in cpi_by_year:
        return cpi_by_year[target_year]
    if target_year < years[0]:
        raise KeyError(
            f"CPI extrapolation backwards not supported: {target_year} < {years[0]}"
        )

    last_year = years[-1]
    if target_year <= last_year:
        # Gap year inside the observed range — interpolate linearly.
        prev = max(y for y in years if y < target_year)
        nxt = min(y for y in years if y > target_year)
        frac = (target_year - prev) / (nxt - prev)
        return cpi_by_year[prev] + frac * (cpi_by_year[nxt] - cpi_by_year[prev])

    # Forward projection: damped-trend over the entire history.
    from census_forecaster import project_damped_trend
    from common.models import AcsObservation

    obs = [
        AcsObservation(
            estimate=cpi_by_year[y],
            moe=0.0,
            year=y,
            vintage="1y",
            geoid="00000",  # placeholder; not consumed by trend math
            indicator="PRICE_INDEX",
        )
        for y in years
    ]
    forecast = project_damped_trend(obs, target_year=target_year)
    if forecast is None:
        raise RuntimeError(
            f"project_damped_trend returned None for price-index target_year={target_year}"
        )
    return float(forecast.point)


# -----------------------------------------------------------------------------
# Multi-county forecast aggregation (the workhorse)
# -----------------------------------------------------------------------------


def _forecast_one_county(
    geoid: str,
    base_year: int,
    target_year: int,
    calibration: dict | None,
) -> tuple[float, float] | None:
    """Run :func:`project_ensemble_multi` for one county and return
    ``(nominal_growth_ratio, log_variance_estimate)``.

    Returns ``None`` if the panel lacks data for this county or the
    ensemble can't produce a forecast. ``log_variance_estimate`` is
    ``(se_total / point)**2`` per the delta method; falls back to a
    sentinel ``float('nan')`` if ``se_total`` is unavailable, so the
    caller can decide how to weight the result.
    """
    from census_forecaster.acs.ensemble import project_ensemble_multi

    series = _load_b19013_series(geoid)
    if not series:
        logger.warning("No B19013 series for geoid=%s in bundled panel", geoid)
        return None

    base_obs = next((o for o in series if o.year == base_year), None)
    if base_obs is None:
        logger.warning(
            "No B19013 obs for geoid=%s in base_year=%s (have %s)",
            geoid, base_year, [o.year for o in series],
        )
        return None
    if base_obs.estimate <= 0:
        logger.warning("Invalid B19013 base value at %s/%s: %s", geoid, base_year, base_obs.estimate)
        return None

    forecast = project_ensemble_multi(
        series_observations=list(series),
        target_year=target_year,
        calibration=calibration,
        use_ml=False,
    )
    if forecast is None:
        logger.warning(
            "project_ensemble_multi returned None for %s/%s→%s",
            geoid, base_year, target_year,
        )
        return None

    nominal_ratio = forecast.point / base_obs.estimate

    # Delta-method log-variance: var(log(X/c)) ≈ (sd(X)/X)^2 for X>0.
    se_total = getattr(forecast, "se_total", None)
    if se_total is None or not math.isfinite(se_total) or forecast.point <= 0:
        log_var = float("nan")
    else:
        log_var = (float(se_total) / float(forecast.point)) ** 2
        if not math.isfinite(log_var) or log_var <= 0:
            log_var = float("nan")

    return nominal_ratio, log_var


def _compute_real_growth_detail(
    geoids: tuple[str, ...],
    base_year: int,
    target_year: int,
    cpi_loader: Callable[[], dict[int, float]],
    weighting: str = "inverse_variance",
) -> RealGrowthDetail | None:
    """Aggregate per-geoid B19013 forecasts into one real growth factor.

    For each ``geoid`` in ``geoids``:

    1. Load B19013 series, find ``base_year`` observation.
    2. Run ``project_ensemble_multi`` → ``forecast.point`` and
       ``forecast.se_total``.
    3. Compute ``nominal_ratio = point / base_obs.estimate``.
    4. Compute weight per ``weighting``:

       - ``"inverse_variance"`` (default): ``1 / (se_total / point)**2``
         (delta-method log-variance). Counties with missing/invalid
         ``se_total`` get equal weight.
       - ``"population"``: ``_COUNTY_POP_2020[geoid]``. Counties with no
         population entry get weight 1.

    Aggregate in log space:

        log_ratio_combined = sum(w_i * log(nominal_ratio_i)) / sum(w_i)
        nominal_growth = exp(log_ratio_combined)

    Then deflate by ``_project_cpi(cpi_loader(), target_year) / cpi_loader()[base_year]``.

    Parameters
    ----------
    geoids:
        Tuple of county GEOIDs to forecast and aggregate. Single-geoid is
        the degenerate case for the Hawaii path.
    base_year:
        Base year of the income value.
    target_year:
        Year to project to. Must be > ``base_year``.
    cpi_loader:
        Zero-arg callable returning a year→index dict (CPI Honolulu, PCE
        national, etc.).
    weighting:
        ``"inverse_variance"`` (default) or ``"population"``.

    Returns
    -------
    float or None
        Real growth factor, or ``None`` if all geoids fail or the CPI
        projection fails. Reasons logged at WARNING level.

    Raises
    ------
    ValueError
        If ``weighting`` is not one of the recognised values.
    """
    if weighting not in {"inverse_variance", "population"}:
        raise ValueError(
            f"weighting must be 'inverse_variance' or 'population', got {weighting!r}"
        )

    if target_year <= base_year:
        return RealGrowthDetail(
            real_factor=1.0, nominal_factor=1.0, inflation_factor=1.0,
            se_log_nominal=0.0, se_log_inflation=0.0, se_log_real=0.0,
            real_ci90_low=1.0, real_ci90_high=1.0,
            inflation_source="none", kappa_used=None, kappa_source=None,
            horizon_months=None,
        )

    if not _ensemble_enabled():
        logger.info("%s set to a falsy value; bypassing ensemble path.", _ENV_VAR)
        return None

    try:
        from census_forecaster.acs.anchors import load_calibration
    except ImportError as e:
        logger.warning(
            "census_forecaster import failed (%s); will fall back to hardcoded growth.", e,
        )
        return None

    try:
        calibration = load_calibration()
    except Exception as e:  # pragma: no cover - load_calibration is robust
        logger.warning("load_calibration failed: %s", e)
        calibration = None

    # Per-county forecasts.
    per_county: list[tuple[str, float, float]] = []  # (geoid, ratio, log_var)
    for geoid in geoids:
        result = _forecast_one_county(geoid, base_year, target_year, calibration)
        if result is None:
            continue
        ratio, log_var = result
        per_county.append((geoid, ratio, log_var))

    if not per_county:
        logger.warning(
            "All %d county forecast(s) failed for base_year=%s, target_year=%s",
            len(geoids), base_year, target_year,
        )
        return None

    # Compute weights.
    if weighting == "inverse_variance":
        # 1/log_var; equal weights for missing-SE counties (NaN log_var).
        finite_vars = [lv for _, _, lv in per_county if math.isfinite(lv) and lv > 0]
        # Use a sentinel "equal" weight equal to the median finite weight,
        # so missing-SE counties don't get pathologically high or low weight.
        if finite_vars:
            sentinel_weight = 1.0 / (sum(finite_vars) / len(finite_vars))
        else:
            sentinel_weight = 1.0
        weights = [
            (1.0 / lv) if (math.isfinite(lv) and lv > 0) else sentinel_weight
            for _, _, lv in per_county
        ]
    else:  # population
        weights = [
            float(_COUNTY_POP_2020.get(geoid, 1))
            for geoid, _, _ in per_county
        ]

    # Weighted geometric mean of ratios = exp(weighted arithmetic mean of logs).
    total_w = sum(weights)
    if total_w <= 0:
        logger.warning("Non-positive total weight for aggregation: %s", total_w)
        return None
    log_ratio_combined = sum(
        w * math.log(ratio) for w, (_, ratio, _) in zip(weights, per_county)
    ) / total_w
    nominal_growth = math.exp(log_ratio_combined)

    # Variance of the combined log-nominal: Var = Σ w_i²σ_i² / (Σ w_i)²,
    # substituting the mean finite variance for counties whose forecast SE
    # was missing (matching the sentinel-weight convention above). For
    # pure inverse-variance weights this collapses to 1/Σw.
    finite_lvs = [lv for _, _, lv in per_county if math.isfinite(lv) and lv > 0]
    se_log_nominal: float | None = None
    if finite_lvs:
        mean_lv = sum(finite_lvs) / len(finite_lvs)
        var_num = sum(
            (w ** 2) * (lv if (math.isfinite(lv) and lv > 0) else mean_lv)
            for w, (_, _, lv) in zip(weights, per_county)
        )
        se_log_nominal = math.sqrt(var_num) / total_w

    # Deflate by price-index ratio.
    # For the Honolulu CPI path: prefer the bundled monthly BLS panel
    # (through the panel's fetch date, typically within 2-3 months of real-time)
    # over the annual-JSON path (ends at 2024). Fall back to the annual path if
    # the BLS panel returns None. For the PCE/national path there is no BLS
    # equivalent, so go directly to the annual-JSON path.
    inflation_factor: float | None = None
    se_log_inflation: float | None = None
    inflation_source = "annual_json"
    kappa_used: float | None = None
    kappa_source: str | None = None
    horizon_months: int | None = None
    if cpi_loader is _load_cpi_honolulu_series:
        cpi_detail = _bls_cpi_ratio_detail(_HONOLULU_BLS_SERIES_ID, base_year, target_year)
        if cpi_detail is None:
            logger.debug(
                "BLS panel CPI unavailable for %s→%s; falling back to annual JSON",
                base_year, target_year,
            )
        else:
            inflation_factor = cpi_detail["ratio"]
            inflation_source = "bls_panel"
            kappa_used = cpi_detail["kappa_used"]
            kappa_source = cpi_detail["kappa_source"]
            horizon_months = cpi_detail["horizon_months"]
            # forecast_se is the κ-rescaled log-space SE; None on
            # exact/interpolated rows, where projection error is zero.
            se_log_inflation = (
                float(cpi_detail["forecast_se"])
                if cpi_detail["forecast_se"] is not None else 0.0
            )

    if inflation_factor is None:
        cpi_by_year = cpi_loader()
        cpi_base = cpi_by_year.get(base_year)
        if cpi_base is None:
            logger.warning(
                "No price-index data for base_year=%s (have %s..%s)",
                base_year, min(cpi_by_year), max(cpi_by_year),
            )
            return None
        try:
            cpi_target = _project_cpi(cpi_by_year, target_year)
        except (KeyError, RuntimeError) as e:
            logger.warning("Price-index projection failed: %s", e)
            return None

        inflation_factor = cpi_target / cpi_base
        if inflation_factor <= 0:
            logger.warning("Non-positive inflation factor %.4f", inflation_factor)
            return None
        # The annual damped-trend path exposes no forecast SE:
        # se_log_inflation stays None and downstream CIs are suppressed
        # rather than understated.

    real_growth = nominal_growth / inflation_factor

    # log(real) = log(nominal) − log(inflation); legs assumed independent
    # (ACS microdata forecast vs BLS price projection), so variances add.
    se_log_real: float | None = None
    real_ci90_low: float | None = None
    real_ci90_high: float | None = None
    if se_log_nominal is not None and se_log_inflation is not None:
        se_log_real = math.sqrt(se_log_nominal ** 2 + se_log_inflation ** 2)
        real_ci90_low = real_growth * math.exp(-_Z_90 * se_log_real)
        real_ci90_high = real_growth * math.exp(+_Z_90 * se_log_real)

    # One-line summary, plus per-county breakdown at DEBUG.
    logger.info(
        "Real income growth %s→%s (n=%d geoids, weighting=%s): "
        "nominal=%.4f (%.2f%%), inflation=%.4f (%.2f%%), real=%.4f (%.2f%%)",
        base_year, target_year, len(per_county), weighting,
        nominal_growth, (nominal_growth - 1) * 100,
        inflation_factor, (inflation_factor - 1) * 100,
        real_growth, (real_growth - 1) * 100,
    )
    if logger.isEnabledFor(logging.DEBUG):
        for geoid, ratio, log_var in per_county:
            logger.debug(
                "  %s: ratio=%.4f, log_var=%s",
                geoid, ratio, f"{log_var:.4e}" if math.isfinite(log_var) else "nan",
            )

    return RealGrowthDetail(
        real_factor=real_growth,
        nominal_factor=nominal_growth,
        inflation_factor=inflation_factor,
        se_log_nominal=se_log_nominal,
        se_log_inflation=se_log_inflation,
        se_log_real=se_log_real,
        real_ci90_low=real_ci90_low,
        real_ci90_high=real_ci90_high,
        inflation_source=inflation_source,
        kappa_used=kappa_used,
        kappa_source=kappa_source,
        horizon_months=horizon_months,
    )


def _compute_real_growth_factor(
    geoids: tuple[str, ...],
    base_year: int,
    target_year: int,
    cpi_loader: Callable[[], dict[int, float]],
    weighting: str = "inverse_variance",
) -> float | None:
    """Point-factor wrapper over :func:`_compute_real_growth_detail`."""
    detail = _compute_real_growth_detail(
        geoids=geoids, base_year=base_year, target_year=target_year,
        cpi_loader=cpi_loader, weighting=weighting,
    )
    return detail.real_factor if detail is not None else None


# -----------------------------------------------------------------------------
# Public wrappers
# -----------------------------------------------------------------------------


@functools.lru_cache(maxsize=16)
def get_hawaii_real_growth_factor(
    base_year: int,
    target_year: int,
    geoid: str = DEFAULT_HAWAII_GEOID,
) -> float | None:
    """Return Hawaii median-income real growth factor from base→target year.

    "Real" means CPI-deflated (purchasing-power-adjusted). The factor
    applied to a base-year income gives the equivalent target-year
    income in constant base-year dollars.

    Parameters
    ----------
    base_year:
        The base year of the income value. Must have a B19013 observation
        in the bundled panel and a Honolulu CPI observation.
    target_year:
        The year to project to. Must be > ``base_year``.
    geoid:
        County GEOID to use as the Hawaii proxy. Defaults to Honolulu
        (15003).

    Returns
    -------
    float or None
        Real growth factor (e.g. 1.05 for 5% real growth), or ``None``
        if the forecast can't be produced. The reason for ``None`` is
        logged at WARNING level.

    Notes
    -----
    Result is memoised via ``@lru_cache``. Delegates to
    :func:`_compute_real_growth_factor` with N=1 geoid and the Honolulu
    CPI loader.
    """
    return _compute_real_growth_factor(
        geoids=(geoid,),
        base_year=base_year,
        target_year=target_year,
        cpi_loader=_load_cpi_honolulu_series,
    )


@functools.lru_cache(maxsize=16)
def get_hawaii_real_growth_detail(
    base_year: int,
    target_year: int,
    geoid: str = DEFAULT_HAWAII_GEOID,
) -> RealGrowthDetail | None:
    """Hawaii real growth factor with its uncertainty decomposition.

    Same computation as :func:`get_hawaii_real_growth_factor`, but returns
    the full :class:`RealGrowthDetail` — nominal/inflation split, log-space
    SEs, and the κ-calibrated 90% CI on the real factor. Consumers that
    only need the point value should keep using the float wrapper.

    Memoised separately from the float wrapper; both delegate to the same
    core, so their point values are always identical.
    """
    return _compute_real_growth_detail(
        geoids=(geoid,),
        base_year=base_year,
        target_year=target_year,
        cpi_loader=_load_cpi_honolulu_series,
    )


@functools.lru_cache(maxsize=8)
def get_national_real_growth_factor(
    base_year: int,
    target_year: int,
    geoids: tuple[str, ...] = _NATIONAL_GEOIDS,
) -> float | None:
    """Return national median-income real growth factor from base→target year.

    "Real" means PCE-deflated (purchasing-power-adjusted). The factor
    applied to a base-year income gives the equivalent target-year income
    in constant base-year dollars.

    Used by :func:`tax_modeler.config.income_growth.apply_income_growth`
    for **nonresident** filers, whose income is earned outside Hawaii and
    therefore tracks national rather than local trends.

    Parameters
    ----------
    base_year:
        Base year of the income value. Must have a B19013 observation in
        the bundled panel for at least one of ``geoids``, and a PCE
        deflator observation.
    target_year:
        The year to project to. Must be > ``base_year``.
    geoids:
        Tuple of county GEOIDs to use as the national basket. Defaults to
        :data:`_NATIONAL_GEOIDS` (8 largest mainland US counties by 2020
        population). Per-county forecasts are aggregated via inverse-
        variance-weighted geometric mean of nominal growth ratios.

    Returns
    -------
    float or None
        Real growth factor (e.g. 1.05 for 5% real growth), or ``None``
        if all county forecasts fail or the PCE projection fails.
        Reasons logged at WARNING level.

    Notes
    -----
    Result is memoised via ``@lru_cache``. Delegates to
    :func:`_compute_real_growth_factor` with the national basket and the
    PCE deflator loader.
    """
    return _compute_real_growth_factor(
        geoids=geoids,
        base_year=base_year,
        target_year=target_year,
        cpi_loader=_load_pce_deflator_series,
    )
