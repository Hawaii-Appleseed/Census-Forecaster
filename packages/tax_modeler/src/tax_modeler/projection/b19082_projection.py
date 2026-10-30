"""B19082_005E top-quintile income share projection (Phase H).

Provides ``compute_top_income_scale`` — the bridge between the ACS B19082_005E
(top-quintile income share) series and the Phase E ``top_income_scale``
parameter in :func:`~tax_modeler.projection.revenue_concentration.expected_revenue_per_filer`.

Background
----------
ACS table B19082 reports the fraction of total aggregate household income
received by each income quintile.  B19082_005E is the top-quintile share —
typically 50–55% for US metropolitan areas, slowly drifting over time.

When the top-quintile share *grows* (top earners pulling away from median),
high-income filers' absolute incomes rise faster than the median.  Because
Hawaii's top 28% of filers generate 81% of income-tax revenue, this
distributional shift has an outsized effect on state revenues.

Phase H integration
-------------------
``compute_top_income_scale`` fetches the B19082_005E projection and computes::

    top_income_scale = projected_top_quintile_share / base_top_quintile_share

where both shares are fractions (0–1).  This value is then passed to::

    expected_revenue_per_filer(projected_median_income, top_income_scale=scale)

The code in ``expected_revenue_per_filer`` scales top brackets by
``base_growth × top_income_scale``, giving total top-bracket income growth
equal to ``(proj_share / base_share) × (proj_median / base_median)`` — the
correct product of the share shift and the median growth.

Data availability
-----------------
B19082_005E is added to ``DIRECT_INDICATORS`` in
``census_forecaster.scripts.build_calibration_panel`` (Phase H), so it will
be populated on the next ``make panel`` rebuild.

Until the panel is rebuilt with a Census API key, ``_load_b19082_series``
returns an empty tuple, ``project_top_quintile_share`` returns ``None``,
and ``compute_top_income_scale`` returns ``None`` — callers fall back to
uniform income growth (``top_income_scale=None`` in
``expected_revenue_per_filer``).  Existing Phase G behaviour is preserved.

Module layout
-------------
``_load_b19082_series(geoid)``
    Load B19082_005E observations from the bundled ACS panel.  Returns ()
    if the indicator is not yet in the panel.
``get_base_top_quintile_share(base_year, geoid)``
    Return the observed B19082_005E fraction for ``base_year``.
``project_top_quintile_share(target_year, geoid, anchor_year)``
    Project B19082_005E to ``target_year`` using a damped-trend projector.
``compute_top_income_scale(target_year, base_year, geoid, anchor_year)``
    Primary API: ``proj_share / base_share``.  Returns ``None`` when data
    is unavailable.
"""
from __future__ import annotations

import functools
import logging
import math
from typing import Optional

logger = logging.getLogger(__name__)

# ACS indicator code for top-quintile income share
B19082_TOP_QUINTILE: str = "B19082_005E"

# Honolulu B19082_005E is published as a percentage (e.g., 51.2 for 51.2%)
# This divisor converts to fraction
_PCT_TO_FRAC: float = 100.0

# Base year used for DOTAX calibration (Phase E)
_DOTAX_BASE_YEAR: int = 2022

# Minimum observations required to run a trend projection
_MIN_OBS_FOR_TREND: int = 5


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

@functools.cache
def _load_b19082_series(geoid: str) -> tuple:
    """Load B19082_005E observations for ``geoid`` from the bundled ACS panel.

    Returns an empty tuple if the indicator is not present in the panel
    (i.e., the panel has not yet been rebuilt with B19082_005E in
    ``DIRECT_INDICATORS``).  This allows callers to degrade gracefully.

    Parameters
    ----------
    geoid:
        ACS FIPS code (e.g., ``"15003"`` for Honolulu County).

    Returns
    -------
    tuple[AcsObservation, ...]
        Sorted by year ascending.  Empty tuple if unavailable.
    """
    from common.models import AcsObservation
    from tax_modeler.projection.acs_supplement_forecast import acs_panel

    try:
        panel = acs_panel()
    except Exception as exc:
        logger.debug("Could not load ACS panel: %s", exc)
        return ()

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
        if o["geoid"] == geoid and o["indicator"] == B19082_TOP_QUINTILE
    ]
    return tuple(sorted(obs, key=lambda o: o.year))


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------

def get_base_top_quintile_share(
    base_year: int = _DOTAX_BASE_YEAR,
    geoid: str = "15003",
) -> Optional[float]:
    """Return observed B19082_005E fraction for ``base_year``.

    Looks for a 1-year ACS vintage at ``base_year``; falls back to the
    closest earlier year if the exact year is unavailable.

    Parameters
    ----------
    base_year:
        Year to look up.  Default 2022 (DOTAX calibration anchor).
    geoid:
        ACS FIPS code.  Default ``"15003"`` (Honolulu).

    Returns
    -------
    float or None
        Top-quintile income share as a fraction in (0, 1), or ``None``
        if no B19082_005E data is available for this geoid.
    """
    from census_forecaster.acs.projection import effective_year

    series = _load_b19082_series(geoid)
    if not series:
        return None

    # Prefer 1-year ACS at base_year; fall back to most-recent ≤ base_year
    one_yr = [o for o in series if o.vintage == "1y" and effective_year(o) == base_year]
    if one_yr:
        raw = one_yr[0].estimate
        return raw / _PCT_TO_FRAC if raw > 1.0 else raw

    earlier = [o for o in series if effective_year(o) <= base_year and o.vintage == "1y"]
    if earlier:
        raw = max(earlier, key=lambda o: effective_year(o)).estimate
        logger.debug(
            "B19082 base_year=%d not found for %s; using most-recent earlier obs",
            base_year, geoid,
        )
        return raw / _PCT_TO_FRAC if raw > 1.0 else raw

    return None


def project_top_quintile_share(
    target_year: int,
    geoid: str = "15003",
    anchor_year: Optional[int] = None,
) -> Optional[float]:
    """Project B19082_005E top-quintile income share to ``target_year``.

    Uses the ``carry_forward`` projector (most robust for slowly-drifting
    shares) when the series is short (< ``_MIN_OBS_FOR_TREND`` years) or
    when the damped-trend fit is ill-conditioned.  Falls back to
    ``damped_log_trend`` for longer series.

    The share is returned as a fraction in (0, 1).

    Parameters
    ----------
    target_year:
        Year to project to.
    geoid:
        ACS FIPS code.
    anchor_year:
        Last year to include in the training series.  Defaults to the
        most-recent 1-year vintage in the panel.

    Returns
    -------
    float or None
        Projected top-quintile share as a fraction, or ``None`` if the
        panel has no B19082_005E data for this geoid.
    """
    from census_forecaster.backtest.acs import DEFAULT_METHODS
    from census_forecaster.acs.projection import effective_year

    series = _load_b19082_series(geoid)
    if not series:
        logger.debug("No B19082_005E data for geoid %r; returning None", geoid)
        return None

    one_yr = [o for o in series if o.vintage == "1y"]
    if not one_yr:
        return None

    if anchor_year is None:
        anchor_year = max(effective_year(o) for o in one_yr)

    train = sorted(
        [o for o in series if effective_year(o) <= anchor_year],
        key=lambda o: (effective_year(o), o.vintage),
    )
    if not train:
        return None

    # Choose projector based on series length
    method = "carry_forward" if len(train) < _MIN_OBS_FOR_TREND else "damped_log_trend"
    projector = DEFAULT_METHODS[method]
    fp = projector(train, target_year)
    if fp is None or not math.isfinite(fp.point) or fp.point <= 0:
        logger.debug(
            "B19082 projector %r returned None/invalid for %s→%d",
            method, geoid, target_year,
        )
        return None

    raw = fp.point
    # Convert from percent (51.2) to fraction (0.512) if needed
    share = raw / _PCT_TO_FRAC if raw > 1.0 else raw

    # Sanity: income share must be in (0, 1)
    if not 0.0 < share < 1.0:
        logger.warning(
            "B19082 projected share %.4f out of (0, 1) for %s→%d; returning None",
            share, geoid, target_year,
        )
        return None

    logger.debug(
        "B19082 projected top-quintile share for %s→%d: %.4f (method=%s)",
        geoid, target_year, share, method,
    )
    return share


# ---------------------------------------------------------------------------
# Primary API
# ---------------------------------------------------------------------------

def compute_top_income_scale(
    target_year: int,
    base_year: int = _DOTAX_BASE_YEAR,
    geoid: str = "15003",
    anchor_year: Optional[int] = None,
) -> Optional[float]:
    """Compute the ``top_income_scale`` argument for ``expected_revenue_per_filer``.

    The scale factor is::

        top_income_scale = projected_top_quintile_share / base_top_quintile_share

    where both shares are fractions (0–1) from B19082_005E.

    When passed to ``expected_revenue_per_filer``, the top brackets (AGI ≥
    $75k) are scaled by ``base_growth × top_income_scale``, so their total
    growth equals::

        (proj_share / base_share) × (proj_median / base_median)

    which is the correct expression for absolute top-quintile income growth
    when the share shifts.

    Returns ``None`` when B19082_005E is not yet in the bundled panel (the
    panel must be rebuilt once ``B19082_005E`` is in ``DIRECT_INDICATORS``).
    Callers should treat ``None`` as "fall back to uniform growth" (pass
    ``top_income_scale=None`` to ``expected_revenue_per_filer``).

    Parameters
    ----------
    target_year:
        Year being projected (e.g., 2026).
    base_year:
        DOTAX calibration base year.  Default 2022.
    geoid:
        ACS FIPS code.  Default ``"15003"`` (Honolulu County).
    anchor_year:
        Last ACS year included in the training series.  Default: latest in
        panel.

    Returns
    -------
    float or None
        ``top_income_scale ≥ 0``, or ``None`` if B19082_005E unavailable.
    """
    base_share = get_base_top_quintile_share(base_year, geoid)
    if base_share is None or base_share <= 0:
        logger.debug(
            "B19082 base share unavailable for geoid=%r base_year=%d",
            geoid, base_year,
        )
        return None

    proj_share = project_top_quintile_share(target_year, geoid, anchor_year)
    if proj_share is None:
        return None

    scale = proj_share / base_share
    logger.info(
        "B19082 top_income_scale for %s %d→%d: base=%.4f proj=%.4f scale=%.4f",
        geoid, base_year, target_year, base_share, proj_share, scale,
    )
    return scale
