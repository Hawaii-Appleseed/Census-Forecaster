"""Revenue-level split-conformal prediction intervals (Phase C).

Extends ``census_forecaster.acs.conformal`` to calibrate directly on
revenue non-conformity scores, providing a distribution-free marginal
coverage guarantee at the revenue level.

Why calibrate at the revenue level?
-------------------------------------
Phase B's delta-method SE propagation (``se_rev = mr × se_income``) is a
first-order approximation that works well near the median income but is
systematically imprecise near bracket boundaries: the marginal rate is
discontinuous, so a small income move near a threshold causes a larger
revenue SE move than the derivative predicts.  Over a panel of B19013
observations, this introduces systematic miscalibration — the CI coverage
is slightly off from the nominal 90%.

Split-conformal calibration (Papadopoulos et al. 2002; Angelopoulos & Bates
2022) is distribution-free: it makes no assumption about the error
distribution and provides a *finite-sample marginal coverage guarantee*.
Applied at the revenue level, it corrects for:

1. Delta-method approximation error near bracket boundaries.
2. Any residual income-forecast bias carried through the tax function.

Non-conformity score
--------------------
For a calibration fold with projected revenue r̂, actual revenue r, and
propagated revenue SE σ_r (from Phase B delta method)::

    s = |r - r̂| / σ_r

The conformal quantile q is the ⌈(n+1)(1-α)⌉-th order statistic of the
calibration scores.  The final CI half-width is::

    half = max(Z₉₀ × σ_r, q × σ_r) = σ_r × max(1.645, q)

If q ≤ Z₉₀ ≈ 1.645 the delta-method CI is already conservative and the
floor does not bind.  If q > Z₉₀, the CI is widened to ensure coverage.

Usage
-----
::

    from tax_modeler.projection.revenue_conformal import (
        calibrate_revenue_conformal,
        apply_revenue_conformal_floor,
        run_calibrated_revenue_backtest,
    )

    # 1. Calibrate on hold-out years (distinct from training anchors).
    quantiles = calibrate_revenue_conformal(
        panel, calibration_anchors=[2016, 2017], horizon=2, methods=methods
    )

    # 2. Evaluate on held-out evaluation anchors.
    summaries = run_revenue_backtest(
        panel, eval_anchors, horizon=2, methods=methods,
        conformal_quantiles=quantiles,
    )

    # 3. Or use the convenience wrapper that does both.
    summaries = run_calibrated_revenue_backtest(
        panel,
        calibration_anchors=[2016, 2017],
        eval_anchors=[2018, 2020, 2022],
        horizon=2,
    )

Module layout
-------------
``RevenueConformalRecord``
    Per (method, h_bucket) conformal quantile.
``compute_revenue_conformal_quantiles(rows, ...)``
    Build conformal quantiles from ``BacktestRow`` residuals.
``apply_revenue_conformal_floor(rev, se_rev, quantiles, method, h)``
    Apply the floor to a single forecast.
``calibrate_revenue_conformal(panel, calibration_anchors, ...)``
    Convenience: run revenue backtest on calibration years, extract quantiles.
``run_calibrated_revenue_backtest(panel, calibration_anchors, eval_anchors, ...)``
    Full calibrate-then-evaluate pipeline in one call.
"""
from __future__ import annotations

import logging
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from census_forecaster.backtest.acs import BacktestRow, BacktestSummary, ProjectorFn

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_Z_90 = 1.645  # 90% Gaussian CI multiplier — matches common.moe.ACS_MOE_Z exactly

# Horizon buckets (mirrors census_forecaster.acs.strata)
_SHORT_MAX = 2   # h ≤ 2 → "short"; h > 2 → "long"
WILDCARD = "*"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RevenueConformalRecord:
    """Per-stratum conformal quantile for revenue forecasting.

    Attributes
    ----------
    method:
        Projector method name (e.g. "ensemble", "carry_forward").
    h_bucket:
        "short" (h ≤ 2), "long" (h > 2), or WILDCARD.
    quantile:
        The conformal q value.  CI half-width = q × se_revenue.
        A value > 1.645 means the delta-method CI was too narrow.
    n_calibration:
        Number of calibration folds used.
    target_coverage:
        Nominal coverage (default 0.90).
    """
    method: str
    h_bucket: str
    quantile: float
    n_calibration: int
    target_coverage: float


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_horizon(h: int) -> str:
    return "short" if h <= _SHORT_MAX else "long"


def _conformal_quantile(scores: list[float], alpha: float = 0.10) -> float:
    """Finite-sample split-conformal quantile.

    Returns the ⌈(n+1)(1-α)⌉-th order statistic of ``scores``, or ∞ when
    the required index exceeds n (coverage guarantee requires adding an ∞
    sentinel, making the interval infinite — caller should treat ∞ as
    "no conformal constraint").
    """
    n = len(scores)
    if n == 0:
        return math.inf
    level = math.ceil((n + 1) * (1.0 - alpha))
    if level > n:
        return math.inf
    return sorted(scores)[level - 1]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_revenue_conformal_quantiles(
    rows: Sequence[BacktestRow],
    target_coverage: float = 0.90,
    n_threshold: int = 10,
) -> list[RevenueConformalRecord]:
    """Compute per-(method, h_bucket) conformal quantiles from backtest rows.

    Non-conformity score for each fold::

        se_rev = sqrt(row.sample_se² + row.forecast_se²)
        s = |row.actual - row.projected| / se_rev

    Rows with zero or non-finite SE are skipped.

    Parameters
    ----------
    rows:
        ``BacktestRow`` objects from ``run_revenue_backtest``; must have
        ``sample_se`` and ``forecast_se`` populated (Phase B always fills them).
    target_coverage:
        Nominal coverage level (default 0.90).
    n_threshold:
        Minimum calibration folds per stratum.  Below this, no record is
        emitted (conformal guarantee unreliable at small n).  Default 10
        (lower than census_forecaster's 20 because revenue strata are smaller).

    Returns
    -------
    list[RevenueConformalRecord]
        One record per (method, h_bucket) stratum with sufficient data.
    """
    alpha = 1.0 - target_coverage

    # Group non-conformity scores by (method, h_bucket)
    groups: dict[tuple[str, str], list[float]] = {}
    for r in rows:
        se_rev = math.sqrt(r.sample_se ** 2 + r.forecast_se ** 2)
        if se_rev <= 0 or not math.isfinite(se_rev):
            continue
        score = abs(r.actual - r.projected) / se_rev
        if not math.isfinite(score):
            continue
        h_bucket = _classify_horizon(r.horizon)
        key = (r.method, h_bucket)
        groups.setdefault(key, []).append(score)

    records: list[RevenueConformalRecord] = []
    for (method, h_bucket), scores in sorted(groups.items()):
        n = len(scores)
        if n < n_threshold:
            logger.debug(
                "Stratum (%s, %s): only %d folds < n_threshold=%d, skipping",
                method, h_bucket, n, n_threshold,
            )
            continue
        q = _conformal_quantile(scores, alpha)
        if not math.isfinite(q):
            logger.debug(
                "Stratum (%s, %s): quantile is ∞ (n+1 > n), skipping",
                method, h_bucket,
            )
            continue
        records.append(RevenueConformalRecord(
            method=method,
            h_bucket=h_bucket,
            quantile=q,
            n_calibration=n,
            target_coverage=target_coverage,
        ))
        logger.info(
            "Revenue conformal [%s, %s]: q=%.3f (n=%d, target=%.0f%%)",
            method, h_bucket, q, n, target_coverage * 100,
        )

    return records


def apply_revenue_conformal_floor(
    projected_revenue: float,
    se_revenue: float,
    quantiles: Sequence[RevenueConformalRecord],
    method: str,
    horizon: int,
) -> tuple[float, float]:
    """Apply the conformal floor to a revenue CI.

    The final CI half-width is::

        half = se_revenue × max(Z₉₀, q)

    where q is the conformal quantile for (method, h_bucket).  If no
    matching record is found, the unmodified delta-method CI is returned.

    Parameters
    ----------
    projected_revenue:
        Point forecast in dollars.
    se_revenue:
        Propagated revenue standard error (from Phase B delta method).
    quantiles:
        Records from ``compute_revenue_conformal_quantiles``.
    method:
        Method name (must match records' ``method`` field).
    horizon:
        Forecast horizon in years (used to classify into h_bucket).

    Returns
    -------
    (ci90_low, ci90_high) : tuple[float, float]
        The conformal-floored 90% prediction interval.
    """
    h_bucket = _classify_horizon(horizon)

    # Find best matching record: exact → wildcard-h → wildcard-method
    q = None
    for rec in quantiles:
        if rec.method != method:
            continue
        if rec.h_bucket == h_bucket or rec.h_bucket == WILDCARD:
            q = rec.quantile
            break

    # Effective multiplier: max of Gaussian Z₉₀ and conformal quantile
    mult = max(_Z_90, q) if (q is not None and math.isfinite(q)) else _Z_90
    half = se_revenue * mult

    return projected_revenue - half, projected_revenue + half


def calibrate_revenue_conformal(
    series_by_key: dict,
    calibration_anchors: Sequence[int],
    horizon: int | None = None,
    horizons: Sequence[int] | None = None,
    methods: dict[str, ProjectorFn] | None = None,
    tax_fn: Callable[[float], float] | None = None,
    marginal_fn: Callable[[float], float] | None = None,
    target_coverage: float = 0.90,
    n_threshold: int = 10,
) -> list[RevenueConformalRecord]:
    """Run revenue backtest on calibration anchors and extract conformal quantiles.

    This is the calibration phase of the split-conformal protocol:

    1. Run ``run_revenue_backtest`` restricted to ``calibration_anchors``.
    2. Pool all fold rows.
    3. Call ``compute_revenue_conformal_quantiles``.

    The resulting records can be passed to ``run_revenue_backtest`` (via its
    ``conformal_quantiles`` parameter) or used directly with
    ``apply_revenue_conformal_floor``.

    Parameters
    ----------
    series_by_key:
        ``{(geoid, indicator): [AcsObservation, ...]}``
    calibration_anchors:
        Anchor years used ONLY for calibration.  These must be disjoint from
        the evaluation anchors — otherwise the conformal guarantee is invalid.
    horizon / horizons:
        Forecast horizon(s) in years.
    methods:
        Projector dict; defaults to ``DEFAULT_METHODS``.
    tax_fn / marginal_fn:
        Passed through to ``run_revenue_backtest``.
    target_coverage:
        Nominal coverage (default 0.90).
    n_threshold:
        Minimum folds per stratum (default 10).

    Returns
    -------
    list[RevenueConformalRecord]
    """
    from .revenue_backtest import run_revenue_backtest

    kwargs: dict = dict(
        series_by_key=series_by_key,
        anchors=calibration_anchors,
    )
    if horizon is not None:
        kwargs["horizon"] = horizon
    if horizons is not None:
        kwargs["horizons"] = horizons
    if methods is not None:
        kwargs["methods"] = methods
    if tax_fn is not None:
        kwargs["tax_fn"] = tax_fn
    if marginal_fn is not None:
        kwargs["marginal_fn"] = marginal_fn

    cal_summaries = run_revenue_backtest(**kwargs)

    # Pool all rows from all methods into one calibration set
    all_rows: list[BacktestRow] = []
    for summary in cal_summaries.values():
        all_rows.extend(summary.rows)

    records = compute_revenue_conformal_quantiles(
        all_rows, target_coverage=target_coverage, n_threshold=n_threshold,
    )
    logger.info(
        "Revenue conformal calibration: %d anchors → %d rows → %d stratum records",
        len(calibration_anchors), len(all_rows), len(records),
    )
    return records


def run_calibrated_revenue_backtest(
    series_by_key: dict,
    calibration_anchors: Sequence[int],
    eval_anchors: Sequence[int],
    horizon: int | None = None,
    horizons: Sequence[int] | None = None,
    methods: dict[str, ProjectorFn] | None = None,
    tax_fn: Callable[[float], float] | None = None,
    marginal_fn: Callable[[float], float] | None = None,
    target_coverage: float = 0.90,
    n_threshold: int = 10,
) -> tuple[list[RevenueConformalRecord], dict[str, BacktestSummary]]:
    """Split-conformal revenue backtest: calibrate then evaluate.

    Implements the full split-conformal protocol:

    1. Calibrate conformal quantiles on ``calibration_anchors`` (hold-out).
    2. Evaluate on ``eval_anchors`` with conformal-floored CIs.

    **Important**: ``calibration_anchors`` and ``eval_anchors`` must be
    disjoint.  Using overlapping sets invalidates the coverage guarantee.

    Parameters
    ----------
    series_by_key:
        ``{(geoid, indicator): [AcsObservation, ...]}``
    calibration_anchors:
        Years used only for calibration.
    eval_anchors:
        Years used only for evaluation.
    horizon / horizons:
        Forecast horizon(s) in years.
    methods:
        Projector dict; defaults to ``DEFAULT_METHODS``.
    tax_fn / marginal_fn:
        Tax function and its derivative; default to
        ``apply_hawaii_tax_brackets`` / ``marginal_rate``.
    target_coverage:
        Nominal coverage for the conformal quantile (default 0.90).
    n_threshold:
        Minimum calibration folds per stratum (default 10).

    Returns
    -------
    (records, summaries) : tuple
        records — list[RevenueConformalRecord] from calibration.
        summaries — dict[method → BacktestSummary] on eval anchors,
            with conformal-floored CIs.
    """
    from .revenue_backtest import _CONCENTRATION_TAX_FN, run_revenue_backtest
    from .revenue_concentration import concentration_marginal_fn

    _tax = tax_fn or _CONCENTRATION_TAX_FN
    _mr_fn = marginal_fn or concentration_marginal_fn

    # Step 1: calibrate
    records = calibrate_revenue_conformal(
        series_by_key,
        calibration_anchors=calibration_anchors,
        horizon=horizon,
        horizons=horizons,
        methods=methods,
        tax_fn=_tax,
        marginal_fn=_mr_fn,
        target_coverage=target_coverage,
        n_threshold=n_threshold,
    )

    # Step 2: evaluate with conformal-floored CIs
    summaries = run_revenue_backtest(
        series_by_key,
        anchors=eval_anchors,
        horizon=horizon,
        horizons=horizons,
        methods=methods,
        tax_fn=_tax,
        marginal_fn=_mr_fn,
        conformal_quantiles=records,
    )

    return records, summaries
