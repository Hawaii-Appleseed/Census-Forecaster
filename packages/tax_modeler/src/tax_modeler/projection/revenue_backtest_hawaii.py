"""Phase B — bundled-panel Hawaii revenue backtest entry point.

Provides :func:`run_hawaii_revenue_backtest` — a one-call harness that wires
together:

* The bundled ACS calibration panel (B19013 for any FIPS county geoid)
* The Phase E DOTAX-calibrated concentration tax model
* The Phase C split-conformal prediction interval protocol
* The Phase F delta-method SE propagation (``concentration_marginal_fn``)

This makes it possible to run a full walk-forward revenue forecast evaluation
against real ACS historical data with a single function call, no external data
required.

Relationship to other backtest modules
---------------------------------------
``run_revenue_backtest`` (``revenue_backtest.py``)
    Low-level engine: takes a pre-built ``series_by_key`` dict.  Use this
    when you already have the panel in memory or need custom geoids.

``run_calibrated_revenue_backtest`` (``revenue_conformal.py``)
    Calibrate-then-evaluate conformal wrapper.  Also takes raw panel.

``run_hawaii_revenue_backtest`` (this module)
    *Bundled-panel entry point*.  Loads Honolulu (or any in-panel geoid),
    selects default anchors, runs the full split-conformal workflow, and
    returns a rich :class:`HawaiiBacktestResult` with pre-computed
    diagnostic metrics.

Anchor selection
----------------
With 14 annual observations (2010–2024, no 2020) the panel supports:

* **h=1**: 12 valid (anchor, anchor+1) pairs
* **h=2**: 11 valid (anchor, anchor+2) pairs

Default split: first half of valid anchors → calibration (conformal
fitting); second half → evaluation (MAPE + CI coverage measurement).
This yields ≈6–7 calibration folds and ≈5–6 evaluation folds per horizon
bucket.  The ``conformal_n_threshold`` parameter (default 4) relaxes the
minimum-fold requirement from the conformal module's default of 10,
matching the 14-year panel size.

Usage
-----
::

    from tax_modeler.projection.revenue_backtest_hawaii import (
        run_hawaii_revenue_backtest,
        HawaiiBacktestResult,
    )

    result = run_hawaii_revenue_backtest()   # Honolulu, horizons=(1, 2)
    print(f"Ensemble MAPE:        {result.ensemble_revenue_mape * 100:.1f}%")
    print(f"CI90 coverage:        {result.ensemble_ci_coverage * 100:.1f}%")
    print(f"MAPE amplification:   {result.ensemble_mape_amplification:.2f}×")
    print(f"Best method:          {result.best_method}")

Module layout
-------------
``HawaiiBacktestResult``
    Frozen dataclass: per-method summaries + conformal records + diagnostics.
``load_series_for_backtest(geoid, indicator)``
    Build the ``{(geoid, indicator): series}`` dict from the bundled panel.
``_select_anchors(series, horizons, min_train_years)``
    Compute calibration and evaluation anchor years automatically.
``run_hawaii_revenue_backtest(geoid, horizons, ...)``
    One-call evaluation entry point.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_GEOID: str = "15003"           # Honolulu County
_DEFAULT_HORIZONS: tuple[int, ...] = (1, 2)
_MIN_TRAIN_YEARS: int = 5               # minimum prior 1y observations required
_CONFORMAL_N_THRESHOLD: int = 4        # relaxed from module default 10; suits 14-yr panel


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HawaiiBacktestResult:
    """Walk-forward revenue backtest result for a single geoid.

    Attributes
    ----------
    geoid:
        ACS FIPS code evaluated (e.g. ``"15003"`` for Honolulu).
    horizons:
        Forecast horizons used (e.g. ``(1, 2)``).
    anchors_cal:
        Anchor years used for conformal calibration (training).
    anchors_eval:
        Anchor years used for out-of-sample evaluation.
    summaries:
        Per-method :class:`~census_forecaster.backtest.acs.BacktestSummary`
        (revenue-level, evaluated on ``anchors_eval``).
    conformal_records:
        Tuple of conformal quantile records used to floor the CIs.  Empty
        when ``use_conformal=False`` or calibration data insufficient.
    income_mape_by_method:
        Income-level MAPE for the same evaluation folds; used to compute
        :attr:`ensemble_mape_amplification`.
    """
    geoid: str
    horizons: tuple[int, ...]
    anchors_cal: tuple[int, ...]
    anchors_eval: tuple[int, ...]
    summaries: dict  # dict[str, BacktestSummary]
    conformal_records: tuple  # tuple[RevenueConformalRecord, ...]
    income_mape_by_method: dict  # dict[str, float]

    # ------------------------------------------------------------------
    # Convenience properties
    # ------------------------------------------------------------------

    @property
    def best_method(self) -> str:
        """Method name with the lowest mean absolute percentage error."""
        if not self.summaries:
            return ""
        return min(
            (m for m in self.summaries if math.isfinite(self.summaries[m].mean_abs_pct_error)),
            key=lambda m: self.summaries[m].mean_abs_pct_error,
            default=next(iter(self.summaries)),
        )

    @property
    def ensemble_revenue_mape(self) -> float:
        """Revenue MAPE for the ``"ensemble"`` method (NaN if absent)."""
        s = self.summaries.get("ensemble")
        return s.mean_abs_pct_error if s is not None else math.nan

    @property
    def ensemble_income_mape(self) -> float:
        """Income MAPE for the ``"ensemble"`` method (NaN if absent)."""
        return self.income_mape_by_method.get("ensemble", math.nan)

    @property
    def ensemble_mape_amplification(self) -> float:
        """Revenue MAPE / income MAPE for the ensemble method.

        For a progressive bracket schedule, values in [1.0, 3.0] are
        economically normal (driven by effective-marginal > effective-average
        rate).  NaN when either MAPE is unavailable or income MAPE is zero.
        """
        rm = self.ensemble_revenue_mape
        im = self.ensemble_income_mape
        if math.isfinite(rm) and math.isfinite(im) and im > 0:
            return rm / im
        return math.nan

    @property
    def ensemble_ci_coverage(self) -> float:
        """CI90 coverage rate for the ``"ensemble"`` method (NaN if absent)."""
        s = self.summaries.get("ensemble")
        return s.ci90_coverage if s is not None else math.nan

    @property
    def n_eval_folds(self) -> int:
        """Number of evaluation folds for the ``"ensemble"`` method."""
        s = self.summaries.get("ensemble")
        return s.n if s is not None else 0


# ---------------------------------------------------------------------------
# Data loading helper
# ---------------------------------------------------------------------------

def load_series_for_backtest(
    geoid: str = _DEFAULT_GEOID,
    indicator: str = "B19013_001E",
) -> dict:
    """Build a ``series_by_key`` dict from the bundled ACS panel.

    Parameters
    ----------
    geoid:
        ACS FIPS code.  Must be present in the bundled calibration panel.
    indicator:
        ACS indicator code.  Default ``"B19013_001E"`` (median household
        income).

    Returns
    -------
    dict[tuple[str, str], tuple[AcsObservation, ...]]
        Ready for :func:`~tax_modeler.projection.revenue_backtest.run_revenue_backtest`.
        Empty dict if ``geoid`` is not in the panel.
    """
    from tax_modeler.projection.income_forecast import _load_b19013_series

    series = _load_b19013_series(geoid)
    if not series:
        logger.warning("No B19013 data for geoid %r in bundled panel", geoid)
        return {}
    return {(geoid, indicator): series}


# ---------------------------------------------------------------------------
# Anchor selection
# ---------------------------------------------------------------------------

def _select_anchors(
    series: tuple,
    horizons: tuple[int, ...],
    min_train_years: int = _MIN_TRAIN_YEARS,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Compute calibration and evaluation anchor years for the bundled panel.

    **Algorithm**:

    1. Collect all 1-year ACS vintage years present in the series.
    2. Keep only anchors where (a) there are at least ``min_train_years``
       prior observations, and (b) at least one ``anchor + h`` year has an
       actual 1-year observation.
    3. Split the valid anchors approximately 50/50: first half →
       calibration, second half → evaluation.

    The 50/50 split ensures the conformal calibration set does not bleed
    into the evaluation set, preserving the distribution-free coverage
    guarantee.

    Parameters
    ----------
    series:
        Sorted tuple of :class:`~common.models.AcsObservation`.
    horizons:
        Forecast horizons in years (e.g. ``(1, 2)``).
    min_train_years:
        Minimum number of 1-year observations required before an anchor
        can be used.  Default 5.

    Returns
    -------
    tuple[tuple[int, ...], tuple[int, ...]]
        ``(calibration_anchors, eval_anchors)`` — both sorted ascending.
    """
    from census_forecaster.acs.projection import effective_year

    avail = sorted(
        set(int(effective_year(o)) for o in series if o.vintage == "1y")
    )
    avail_set = set(avail)

    valid: list[int] = []
    for i, anchor in enumerate(avail):
        if i < min_train_years:
            continue  # not enough prior observations
        if any((anchor + h) in avail_set for h in horizons):
            valid.append(anchor)

    if not valid:
        return (), ()

    n = len(valid)
    split = max(1, n // 2)
    return tuple(valid[:split]), tuple(valid[split:])


# ---------------------------------------------------------------------------
# One-call evaluation entry point
# ---------------------------------------------------------------------------

def run_hawaii_revenue_backtest(
    geoid: str = _DEFAULT_GEOID,
    horizons: Sequence[int] | None = None,
    calibration_anchors: Sequence[int] | None = None,
    eval_anchors: Sequence[int] | None = None,
    use_conformal: bool = True,
    min_train_years: int = _MIN_TRAIN_YEARS,
    conformal_n_threshold: int = _CONFORMAL_N_THRESHOLD,
    tax_fn: Callable[[float], float] | None = None,
    marginal_fn: Callable[[float], float] | None = None,
) -> HawaiiBacktestResult | None:
    """Run a full walk-forward revenue backtest on the bundled ACS panel.

    Steps
    -----
    1. Load the B19013 series for ``geoid`` from the bundled calibration
       panel.
    2. Compute valid anchor years (automatically or from caller arguments).
    3. If ``use_conformal=True``, run the full split-conformal protocol:
       calibrate conformal quantiles on the first half of anchors, then
       evaluate on the second half with conformal-floored CIs.
       If ``use_conformal=False``, run a plain delta-method backtest on
       ``eval_anchors`` (no conformal floor).
    4. Run a parallel income-level backtest on the same evaluation folds to
       compute MAPE amplification.
    5. Return a :class:`HawaiiBacktestResult` with all diagnostics.

    Parameters
    ----------
    geoid:
        ACS FIPS code.  Default ``"15003"`` (Honolulu County).
    horizons:
        Forecast horizons in years.  Default ``(1, 2)``.
    calibration_anchors:
        Anchor years reserved for conformal calibration.  Auto-selected
        (first half of valid anchors) when ``None``.  Ignored when
        ``use_conformal=False``.
    eval_anchors:
        Anchor years used for out-of-sample evaluation.  Auto-selected
        (second half of valid anchors) when ``None``.
    use_conformal:
        When ``True`` (default), calibrate conformal quantiles and apply
        the conformal floor to evaluation CIs.  When ``False``, use a
        plain Gaussian delta-method CI (``Z₉₀ × se_rev``).
    min_train_years:
        Minimum prior 1-year observations needed before any anchor.
        Default 5.
    conformal_n_threshold:
        Minimum calibration folds required per (method, h_bucket) stratum
        to compute a conformal quantile.  Default 4 (relaxed from the
        module default of 10 to match the 14-year bundled panel).
    tax_fn:
        Income → per-filer revenue function.  Default: Phase E
        concentration model (``make_concentration_adjusted_fn()``).
    marginal_fn:
        Income → effective marginal function.  Default:
        ``concentration_marginal_fn``.

    Returns
    -------
    HawaiiBacktestResult or None
        ``None`` if ``geoid`` is not in the bundled panel or no valid
        anchor/target pairs exist.
    """
    from census_forecaster.backtest.acs import DEFAULT_METHODS, run_backtest

    from tax_modeler.projection.revenue_backtest import (
        _CONCENTRATION_TAX_FN,
        run_revenue_backtest,
    )
    from tax_modeler.projection.revenue_concentration import concentration_marginal_fn
    from tax_modeler.projection.revenue_conformal import run_calibrated_revenue_backtest

    _tax = tax_fn or _CONCENTRATION_TAX_FN
    _mr = marginal_fn or concentration_marginal_fn
    _horizons: tuple[int, ...] = tuple(horizons) if horizons is not None else _DEFAULT_HORIZONS

    # 1. Load panel
    series_by_key = load_series_for_backtest(geoid)
    if not series_by_key:
        return None

    # The single series for this geoid
    series = next(iter(series_by_key.values()))

    # 2. Resolve anchors
    if calibration_anchors is None or eval_anchors is None:
        auto_cal, auto_eval = _select_anchors(series, _horizons, min_train_years)
        _cal_anchors = tuple(calibration_anchors) if calibration_anchors is not None else auto_cal
        _eval_anchors = tuple(eval_anchors) if eval_anchors is not None else auto_eval
    else:
        _cal_anchors = tuple(calibration_anchors)
        _eval_anchors = tuple(eval_anchors)

    if not _eval_anchors:
        logger.warning("No valid evaluation anchors for geoid %r; returning None", geoid)
        return None

    logger.info(
        "Phase B backtest [%s] horizons=%s cal_anchors=%s eval_anchors=%s",
        geoid, _horizons, _cal_anchors, _eval_anchors,
    )

    # 3. Revenue backtest (with or without conformal)
    if use_conformal and _cal_anchors:
        conformal_records_list, eval_summaries = run_calibrated_revenue_backtest(
            series_by_key=series_by_key,
            calibration_anchors=list(_cal_anchors),
            eval_anchors=list(_eval_anchors),
            horizons=list(_horizons),
            tax_fn=_tax,
            marginal_fn=_mr,
            n_threshold=conformal_n_threshold,
        )
        conformal_records: tuple = tuple(conformal_records_list)
    else:
        eval_summaries = run_revenue_backtest(
            series_by_key=series_by_key,
            anchors=list(_eval_anchors),
            horizons=list(_horizons),
            tax_fn=_tax,
            marginal_fn=_mr,
        )
        conformal_records = ()

    if not eval_summaries:
        logger.warning("Revenue backtest produced no summaries for geoid %r", geoid)
        return None

    # 4. Income-level backtest on same evaluation folds (for amplification)
    income_summaries = run_backtest(
        series_by_key,
        list(_eval_anchors),
        horizons=list(_horizons),
        methods=DEFAULT_METHODS,
    )
    income_mape_by_method: dict[str, float] = {
        m: s.mean_abs_pct_error for m, s in income_summaries.items()
    }

    # 5. Log summary
    for method, s in eval_summaries.items():
        if s.n > 0:
            im = income_mape_by_method.get(method, math.nan)
            amp = (s.mean_abs_pct_error / im) if (math.isfinite(im) and im > 0) else math.nan
            logger.info(
                "  [%s] n=%d  rev_MAPE=%.2f%%  inc_MAPE=%.2f%%  "
                "amp=%.2f×  CI90=%.1f%%",
                method, s.n,
                s.mean_abs_pct_error * 100,
                im * 100 if math.isfinite(im) else float("nan"),
                amp,
                s.ci90_coverage * 100,
            )

    return HawaiiBacktestResult(
        geoid=geoid,
        horizons=_horizons,
        anchors_cal=_cal_anchors,
        anchors_eval=_eval_anchors,
        summaries=eval_summaries,
        conformal_records=conformal_records,
        income_mape_by_method=income_mape_by_method,
    )
