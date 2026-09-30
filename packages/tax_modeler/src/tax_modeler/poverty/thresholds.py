"""Hawaii Supplemental Poverty Measure thresholds.

Real SPM thresholds are published annually by Census in the P60 SPM
series and are *geographically adjusted* via a metropolitan-area
housing-cost index. Hawaii (Honolulu MSA) sits well above the contiguous-
US baseline — historically ~14-18% higher for renters.

This module ships an approximate lookup table for the most recent
publication years and a function that interpolates by household
composition. Years past the table raise unless the caller passes
``extrapolate=True``, which indexes the latest tabled year forward by the
same CPI assumption used for projected-year credit parameters. Replace the
table with a CSV import (and read it via
``state_config.HAWAII.spm_thresholds_csv``) when production-grade
analysis is needed; the API contract here will not change.

Sources to verify against when promoting Phase 2 to production:
- US Census Bureau, "The Supplemental Poverty Measure" (P60-280 series)
- Census API ``acs/acs1/subject`` table S1701 cross-referenced with
  metropolitan-area FMR adjustment factors.
"""
from __future__ import annotations

from typing import Literal

from tax_modeler.errors import ConfigError

Tenure = Literal["renter", "owner_with_mortgage", "owner_no_mortgage"]


# Census P60-283 (2024 SPM) two-adult-two-child base thresholds for
# renters in metropolitan areas, contiguous US. Other compositions are
# computed via the standard SPM equivalence scale.
#
# 2025 value is a *projection* (2024 × 1.03, mirroring the 2.5-3% CPI-U
# growth in the published series); replace with the actual Census
# release when P60-284 ships in Oct 2026.
_BASE_THRESHOLD_2A2C_RENTER = {
    2022: 31_312,
    2023: 33_148,
    2024: 34_393,
    2025: 35_425,  # projected: 2024 × 1.03
}

# Honolulu MSA Median Rent Index (Renwick 2011 + 2015 ACS update; Census
# applies this only to the *housing portion* of the SPM threshold, not the
# whole threshold). 1.62 reflects Honolulu MSA 2BR median rent vs. national
# metropolitan-area 2BR median rent.
_HAWAII_MRI = 1.62

# Housing-share of the SPM threshold by tenure (Burns & Fox 2021 SEHSD WP21-17,
# Table 1; values used by Census from TY2019 onward). The non-housing share
# (food, clothing, utilities, etc.) is constant across geographies — only
# the housing portion is geographically adjusted.
_HOUSING_SHARE_BY_TENURE: dict[Tenure, float] = {
    "renter": 0.442,
    "owner_with_mortgage": 0.440,
    "owner_no_mortgage": 0.333,
}

# Tenure scaling — the threshold base is published for renters; owners with
# no mortgage get a lower base because their housing portion excludes
# mortgage P&I. Renwick 2017 reports ~84% of renter housing portion.
_TENURE_BASE_SCALE: dict[Tenure, float] = {
    "renter": 1.00,
    "owner_with_mortgage": 1.00,
    "owner_no_mortgage": 0.84,
}


def _base_threshold_2a2c_renter(year: int, *, extrapolate: bool = False) -> float:
    """Contiguous-US 2A2C renter base threshold for ``year``.

    Tabled years return the table value. With ``extrapolate=True``, a year
    past the last tabled year indexes that year's value forward by
    ``CREDIT_PARAM_CPI_GROWTH``/yr, compounded: the chained-CPI assumption
    the projector and ``compute_base_tax`` use to extrapolate federal credit
    and bracket parameters past the last Rev. Proc., so a projected-year
    poverty run prices its threshold in the same dollars as its credits.
    The published SPM thresholds have grown faster than chained CPI
    (TY2022-2024: +4.8%/yr), so the extrapolated threshold is if anything
    low. Anything else raises ``ConfigError``; nothing extrapolates unless
    the caller asks.
    """
    if year in _BASE_THRESHOLD_2A2C_RENTER:
        return float(_BASE_THRESHOLD_2A2C_RENTER[year])
    last = max(_BASE_THRESHOLD_2A2C_RENTER)
    if extrapolate and year > last:
        from tax_modeler.credits.eitc import CREDIT_PARAM_CPI_GROWTH

        return float(
            _BASE_THRESHOLD_2A2C_RENTER[last] * (1.0 + CREDIT_PARAM_CPI_GROWTH) ** (year - last)
        )
    raise ConfigError(
        f"No Hawaii SPM threshold available for year={year}"
        + ("" if extrapolate else " (pass extrapolate=True for later years)"),
        available=sorted(_BASE_THRESHOLD_2A2C_RENTER),
    )


def _hawaii_geo_multiplier(tenure: Tenure) -> float:
    """Effective Honolulu MSA threshold multiplier for a given tenure.

    Census applies the Median Rent Index only to the housing portion of
    the SPM threshold. The effective multiplier on the full threshold is::

        effective_multiplier = (1 - housing_share) + housing_share * MRI

    For Honolulu (MRI ≈ 1.62):
      * renter:              1 + 0.442 * 0.62 ≈ 1.274
      * owner_with_mortgage: 1 + 0.440 * 0.62 ≈ 1.273
      * owner_no_mortgage:   1 + 0.333 * 0.62 ≈ 1.207

    The previous implementation used a flat 1.18 multiplier across tenures,
    which underestimated thresholds for Hawaii renters (true effective
    ratio ~1.27) and overestimated for owners-no-mortgage when combined
    with the separate _TENURE_BASE_SCALE of 0.84 (yielding 0.99 net).
    """
    share = _HOUSING_SHARE_BY_TENURE.get(tenure, _HOUSING_SHARE_BY_TENURE["renter"])
    return (1.0 - share) + share * _HAWAII_MRI


def _equivalence_scale(n_adults: int, n_children: int) -> float:
    """SPM three-parameter (Betson 1996) equivalence scale, normalized to (2A, 2C).

    Census SPM Technical Documentation §4.2.1:

      * One- and two-adult units (no children): scale = (adults)^0.5
      * Single parents (1 adult + N children):
            scale = (1 + 0.8 + 0.5*(N-1))^0.7   for N >= 1
      * All other families with children:
            scale = (adults + 0.5*children)^0.7

    The reference family (2 adults + 2 children) has raw scale
    ``(2 + 0.5*2)^0.7 = 3^0.7 ≈ 2.158``; returned scale is normalized
    by that reference so the 2A2C family always evaluates to 1.0.
    """
    if n_adults < 1:
        raise ConfigError("n_adults must be >= 1")
    if n_children < 0:
        raise ConfigError("n_children must be >= 0")

    if n_children == 0:
        # One- and two-adult units use the Betson "adults only" scale.
        raw = float(n_adults) ** 0.5
    elif n_adults == 1:
        # Single-parent families: first child weighted 0.8, others 0.5.
        raw = (1.0 + 0.8 + 0.5 * (n_children - 1)) ** 0.7
    else:
        # All other families with children.
        raw = (n_adults + 0.5 * n_children) ** 0.7

    base = (2 + 0.5 * 2) ** 0.7  # = 3 ** 0.7
    return raw / base


def threshold_for_units(
    units,
    *,
    year: int = 2024,
    tenure_col: str = "tenure",
    n_adults_col: str = "n_adults",
    n_children_col: str = "n_children",
    extrapolate: bool = False,
):
    """Vectorized per-row SPM threshold lookup using composition + tenure.

    Two composition-input modes, auto-detected:

      * **SPM-unit mode** (preferred): if ``n_adults_col`` AND
        ``n_children_col`` are both present on the input frame, they are
        used directly. This is the correct mode for SPM-unit-grained
        analysis where the composition is computed from PUMS persons,
        not derived from filing_status.
      * **Tax-unit fallback**: if either composition column is absent,
        the legacy filing_status-based approximation is used:
        ``n_adults = 1 + (filing_status == "married_filing_jointly")``,
        ``n_children = num_dependents``. This preserves backward
        compatibility for callers that pass the tax-unit frame directly
        (e.g. revenue forecast scripts).

    Falls back to ``"renter"`` when ``tenure`` is missing — the most
    conservative assumption (renter thresholds are highest).

    ``extrapolate`` is passed to :func:`hawaii_spm_threshold`: off by
    default, so a year past the table raises.
    """
    import numpy as np
    import pandas as pd

    if n_adults_col in units.columns and n_children_col in units.columns:
        n_adults = units[n_adults_col].fillna(0).astype(int).clip(lower=1).to_numpy()
        n_children = units[n_children_col].fillna(0).astype(int).clip(lower=0).to_numpy()
    else:
        is_joint = (units["filing_status"] == "married_filing_jointly").to_numpy()
        n_adults = 1 + is_joint.astype(int)
        n_children = units["num_dependents"].fillna(0).astype(int).to_numpy()
    if tenure_col in units.columns:
        tenures = units[tenure_col].fillna("renter").astype(str).to_numpy()
    else:
        tenures = np.array(["renter"] * len(units))

    out = np.zeros(len(units))
    for i in range(len(units)):
        ten = tenures[i]
        if ten not in _TENURE_BASE_SCALE:
            ten = "renter"
        out[i] = hawaii_spm_threshold(
            year,
            n_adults=int(n_adults[i]),
            n_children=int(n_children[i]),
            tenure=ten,  # type: ignore[arg-type]
            extrapolate=extrapolate,
        )
    return pd.Series(out, index=units.index)


def hawaii_spm_threshold(
    year: int,
    *,
    n_adults: int = 2,
    n_children: int = 2,
    tenure: Tenure = "renter",
    extrapolate: bool = False,
) -> float:
    """Approximate Hawaii SPM threshold for a household of the given composition.

    Returns annual dollar threshold below which the household is in SPM
    poverty.

    Parameters
    ----------
    year:
        Calendar year. Must be in the available table unless
        ``extrapolate`` is set.
    n_adults, n_children:
        Household composition. Defaults match the Census base reference
        unit (2 adults + 2 children) so calling with no args returns the
        published two-adult-two-child threshold.
    tenure:
        ``"renter"``, ``"owner_with_mortgage"``, or ``"owner_no_mortgage"``.
    extrapolate:
        For a year past the table, index the latest tabled year forward by
        the credit-parameter CPI assumption (see
        :func:`_base_threshold_2a2c_renter`) instead of raising. Off by
        default.
    """
    base = _base_threshold_2a2c_renter(year, extrapolate=extrapolate)
    if tenure not in _TENURE_BASE_SCALE:
        raise ConfigError(
            f"Unknown tenure={tenure!r}",
            available=sorted(_TENURE_BASE_SCALE),
        )
    # Census applies the geographic multiplier per-tenure (housing-share
    # weighted) — not the flat constant the previous implementation used.
    geo_adj = _hawaii_geo_multiplier(tenure)
    return float(
        base
        * geo_adj
        * _TENURE_BASE_SCALE[tenure]
        * _equivalence_scale(n_adults, n_children)
    )


def spm_threshold_table(
    year: int, *, extrapolate: bool = False,
) -> dict[tuple[int, int, Tenure], float]:
    """Pre-computed (n_adults, n_children, tenure) → threshold dict for vectorized lookup.

    Useful when assigning thresholds to a unit DataFrame:

    >>> table = spm_threshold_table(2024)
    >>> df["spm_threshold"] = df.apply(
    ...     lambda r: table[(r.n_adults, r.n_children, r.tenure)], axis=1
    ... )
    """
    out: dict[tuple[int, int, Tenure], float] = {}
    for n_adults in range(1, 7):
        for n_children in range(0, 7):
            for tenure in ("renter", "owner_with_mortgage", "owner_no_mortgage"):
                out[(n_adults, n_children, tenure)] = hawaii_spm_threshold(
                    year, n_adults=n_adults, n_children=n_children, tenure=tenure,  # type: ignore[arg-type]
                    extrapolate=extrapolate,
                )
    return out
