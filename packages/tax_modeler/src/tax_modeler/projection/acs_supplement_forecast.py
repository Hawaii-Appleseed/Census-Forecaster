"""Forward projections for ACS housing/poverty indicators used in deduction/credit modeling.

Three indicators are forecast per county:

  - **B25064_001E** (median gross rent): Growth factor available for future
    scaling of renter-credit phase-out thresholds. Not yet consumed by any
    adjustment function — included so callers have the data when the
    renters-credit logic is extended.

  - **B25077_001E** (median home value): Primary consumer is
    :func:`scale_mortgage_deductions`, which multiplies every tier in
    ``mortgage_interest_tiers`` by the home-value growth factor so that
    deduction amounts stay anchored to the local housing market rather than
    being frozen at the 2022 SOI calibration year.

  - **S1701_C03_001E** (poverty rate 0–1): Growth factor available for future
    EITC/low-income-credit eligibility adjustment. Included for completeness;
    no current adjustment function consumes it.

The primary external entry points are:

  - :func:`project_acs_supplement` — produces an :class:`AcsSupplement`
    carrying all three indicators for a given county and target year.
  - :func:`scale_mortgage_deductions` — thin helper that applies the B25077
    factor to a raw params dict, returning a scaled copy.

The latter is called by
:func:`tax_modeler.adjustments.itemized_deductions.scale_deduction_params_for_target_year`.

Notes on the ensemble for non-income indicators
------------------------------------------------
``project_ensemble_multi`` is calibrated primarily for B19013 (median
household income), but its damped-trend + AR(1) backbone applies to any ACS
level series. The QCEW/CPI/PCE anchor components will have low weight for
housing and poverty indicators — which is the right behaviour, since those
anchors are income-specific. What remains is a regularised trend extrapolation
with sensible 90% CIs, which is adequate for scaling deduction parameters.

Kalawao County (15005) has ~90 residents and is absent from the bundled
panel. It falls back to Maui County (15009).
"""
from __future__ import annotations

import copy
import functools
import json
import logging
from dataclasses import dataclass
from importlib.resources import files
from typing import Optional

logger = logging.getLogger(__name__)

# All three supplement indicators.
_SUPPLEMENT_INDICATORS: tuple[str, ...] = (
    "B25064_001E",    # median gross rent
    "B25077_001E",    # median home value
    "S1701_C03_001E", # poverty rate
)

# Kalawao (15005) → Maui (15009) proxy.
_KALAWAO_PROXY = "15009"


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AcsSupplement:
    """Projected ACS housing/poverty values and growth factors for one county.

    All levels are in the natural units of their indicator ($ for rent/home
    value, fraction 0–1 for poverty rate). All growth factors are nominal
    ratios (``projected / anchor``); callers apply their own deflation when
    needed. For mortgage deduction scaling, nominal factors are appropriate
    because the deduction amounts in the config are nominal dollars.

    Fields are ``None`` when the panel lacks data or the ensemble returns
    ``None`` for that indicator.
    """

    geoid: str
    anchor_year: int
    target_year: int

    # B25064_001E — median gross rent ($/month)
    rent_level: Optional[float]
    rent_growth_factor: Optional[float]

    # B25077_001E — median home value ($)
    home_value_level: Optional[float]
    home_value_growth_factor: Optional[float]

    # S1701_C03_001E — poverty rate (fraction, 0–1)
    poverty_rate_level: Optional[float]
    poverty_rate_growth_factor: Optional[float]


# ---------------------------------------------------------------------------
# Panel loader (memoised, generic)
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=32)
def _load_acs_series(geoid: str, indicator: str) -> tuple:
    """Load and cache an ACS indicator series for ``geoid`` from the bundled panel.

    Returns an empty tuple when the geoid/indicator combination is absent.
    Observations are sorted ascending by year.
    """
    from common.models import AcsObservation

    panel_path = (
        files("census_forecaster") / "data" / "calibration_panel" / "acs_panel.json"
    )
    with panel_path.open() as fh:
        panel = json.load(fh)

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
        if o["geoid"] == geoid and o["indicator"] == indicator
    ]
    return tuple(sorted(obs, key=lambda o: o.year))


# ---------------------------------------------------------------------------
# Single-indicator projection (private)
# ---------------------------------------------------------------------------


def _project_one_indicator(
    geoid: str,
    indicator: str,
    target_year: int,
    anchor_year: Optional[int],
    calibration,
) -> Optional[tuple[float, float, int]]:
    """Project one ACS indicator for ``geoid`` to ``target_year``.

    Returns ``(projected_level, growth_factor, actual_anchor_year)`` or
    ``None`` if the panel or ensemble fails for this indicator.

    ``growth_factor = projected_level / anchor_level`` where
    ``anchor_level`` is the estimate at ``anchor_year`` (defaults to the
    most recent year in the series when ``anchor_year`` is ``None``).
    """
    from census_forecaster.acs.ensemble import project_ensemble_multi

    series = _load_acs_series(geoid, indicator)
    if not series:
        logger.warning(
            "No %s series for geoid=%s in bundled panel", indicator, geoid
        )
        return None

    actual_anchor_year: int = anchor_year if anchor_year is not None else series[-1].year

    anchor_obs = next((o for o in series if o.year == actual_anchor_year), None)
    if anchor_obs is None:
        logger.warning(
            "No %s obs for geoid=%s in anchor_year=%s (have years %s)",
            indicator, geoid, actual_anchor_year,
            [o.year for o in series],
        )
        return None
    if anchor_obs.estimate <= 0:
        logger.warning(
            "Non-positive %s anchor at geoid=%s year=%s: %s",
            indicator, geoid, actual_anchor_year, anchor_obs.estimate,
        )
        return None

    if target_year <= actual_anchor_year:
        return float(anchor_obs.estimate), 1.0, actual_anchor_year

    forecast = project_ensemble_multi(
        series_observations=list(series),
        target_year=target_year,
        calibration=calibration,
        use_ml=False,
    )
    if forecast is None:
        logger.warning(
            "project_ensemble_multi returned None for %s geoid=%s %s→%s",
            indicator, geoid, actual_anchor_year, target_year,
        )
        return None

    level = float(forecast.point)
    factor = level / float(anchor_obs.estimate)
    return level, factor, actual_anchor_year


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=256)
def project_acs_supplement(
    target_year: int,
    geoid: str,
    anchor_year: Optional[int] = None,
) -> AcsSupplement:
    """Project B25064, B25077, and S1701 for ``geoid`` to ``target_year``.

    Indicators that fail (panel gap, ensemble returning None) yield ``None``
    in the corresponding :class:`AcsSupplement` fields. The supplement itself
    is always returned (never ``None``), even when all three indicators fail.

    Parameters
    ----------
    target_year:
        Year to project to.
    geoid:
        Hawaii county FIPS (15001, 15003, 15005, 15007, 15009). Kalawao
        (15005) is proxied to Maui (15009).
    anchor_year:
        Observation year used as the base for growth factors. Defaults to
        the most recent observed year in the panel (currently 2024).

    Returns
    -------
    AcsSupplement
        Container of projected levels and nominal growth factors. Cached per
        process: the result is frozen and depends only on the bundled panel
        and anchors, and the projector asks for the same county-years on
        every projection.
    """
    lookup_geoid = _KALAWAO_PROXY if geoid == "15005" else geoid

    try:
        from census_forecaster.acs.anchors import load_calibration
        calibration = load_calibration()
    except Exception as exc:
        logger.warning("load_calibration failed: %s; proceeding without anchors", exc)
        calibration = None

    _FIELDS = (
        ("B25064_001E",    "rent"),
        ("B25077_001E",    "home_value"),
        ("S1701_C03_001E", "poverty_rate"),
    )

    levels: dict[str, Optional[float]] = {}
    factors: dict[str, Optional[float]] = {}
    resolved_anchor: int = anchor_year or target_year - 1

    for indicator, key in _FIELDS:
        result = _project_one_indicator(
            lookup_geoid, indicator, target_year, anchor_year, calibration
        )
        if result is not None:
            lvl, fac, resolved_anchor = result
            levels[key] = lvl
            factors[key] = fac
        else:
            levels[key] = None
            factors[key] = None

    logger.info(
        "ACS supplement geoid=%s %s→%s: "
        "rent×%.3f home_value×%.3f poverty_rate×%.3f",
        geoid, resolved_anchor, target_year,
        factors.get("rent") or float("nan"),
        factors.get("home_value") or float("nan"),
        factors.get("poverty_rate") or float("nan"),
    )

    return AcsSupplement(
        geoid=geoid,
        anchor_year=resolved_anchor,
        target_year=target_year,
        rent_level=levels["rent"],
        rent_growth_factor=factors["rent"],
        home_value_level=levels["home_value"],
        home_value_growth_factor=factors["home_value"],
        poverty_rate_level=levels["poverty_rate"],
        poverty_rate_growth_factor=factors["poverty_rate"],
    )


def scale_mortgage_deductions(
    params: dict,
    target_year: int,
    geoid: str = "15003",
    anchor_year: Optional[int] = None,
) -> dict:
    """Return a copy of ``params`` with ``mortgage_interest_tiers`` scaled to ``target_year``.

    Uses the B25077 (median home value) nominal growth factor from
    :func:`project_acs_supplement`. Falls back silently to the original
    params if the forecast is unavailable.

    Parameters
    ----------
    params:
        Raw deduction params dict (from ``_load_params()`` or equivalent).
        Not mutated; a deep copy is returned.
    target_year:
        Year to project mortgage amounts to.
    geoid:
        Hawaii county FIPS used for the B25077 lookup. Defaults to Honolulu
        (15003), the most data-rich county and the state-wide proxy.
    anchor_year:
        Anchor year for the growth factor computation. Defaults to the most
        recent observed year in the bundled panel.

    Returns
    -------
    dict
        A deep copy of ``params`` with all ``base_interest`` values in
        ``mortgage_interest_tiers.tiers`` and ``default_base_interest``
        multiplied by the B25077 growth factor. Amounts are rounded to the
        nearest dollar.
    """
    supplement = project_acs_supplement(target_year, geoid, anchor_year)

    if supplement.home_value_growth_factor is None:
        logger.warning(
            "B25077 forecast unavailable for geoid=%s target_year=%s; "
            "mortgage_interest_tiers unchanged",
            geoid, target_year,
        )
        return params

    factor = supplement.home_value_growth_factor
    scaled = copy.deepcopy(params)

    mort = scaled["mortgage_interest_tiers"]
    for tier in mort["tiers"]:
        tier["base_interest"] = round(tier["base_interest"] * factor)
    mort["default_base_interest"] = round(mort["default_base_interest"] * factor)

    logger.debug(
        "Scaled mortgage_interest_tiers by B25077 factor=%.4f "
        "(geoid=%s, %s→%s): default $%d → $%d",
        factor, geoid,
        supplement.anchor_year, target_year,
        params["mortgage_interest_tiers"]["default_base_interest"],
        mort["default_base_interest"],
    )

    return scaled
