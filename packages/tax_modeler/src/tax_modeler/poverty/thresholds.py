"""Hawaii Supplemental Poverty Measure thresholds.

The SPM line is BLS's base threshold for a two-adult, two-child unit, one per
housing tenure, with its housing portion adjusted for local rents and then
scaled to the unit's composition:

    threshold = base[year][tenure]
                * ((1 - housing_share[tenure]) + housing_share[tenure] * MRI[area])
                * equivalence_scale(adults, children)

* **Base thresholds**: BLS research SPM thresholds, the ones the Census Bureau
  uses (bls.gov/pir/spmhome.htm): 2021-2024 as corrected on July 17, 2026,
  2025 as published on August 24, 2026.
* **Housing share and Median Rent Index (MRI)**: the Census Bureau adjusts only
  the housing portion (shelter and utilities, not telephone or internet) by the
  area's median two-bedroom gross rent over the national median (SPM Technical
  Documentation 4.2.2). Hawaii has three SPM areas: Urban Honolulu (Honolulu
  County), Kahului-Wailuku (Maui County) and the state's nonmetropolitan rest
  (Hawaii and Kauai Counties, Kalawao). The indexes are Census's 2025 values and
  the shares the ones that reproduce its published 2025 Hawaii thresholds (P60-290
  Table 19, "SPM Thresholds by Metro Area: 2025") to within a dollar in all nine
  area-tenure cells; ``tests/tax_modeler/poverty/test_thresholds.py`` pins them.
  Both are held fixed in other years.
* **Equivalence scale**: the SPM three-parameter scale (Technical Documentation
  4.2.1).

Households that pay no rent take the owner-without-mortgage threshold, as Census
assigns them ("those who have substantially lower housing costs, either because
they do not pay rent or are owners without a mortgage", P60-290).

Years past the table raise unless the caller passes ``extrapolate=True``, which
indexes the latest tabled year forward by the CPI assumption used for
projected-year credit parameters.

Before November 2026 this module used a renter base 8-12% below BLS's (source
unknown), one rent index of 1.62 for the whole state, an owner-without-mortgage
base of 0.84 of the renter's, and the adults-only scale ``adults**0.5`` for units
of any number of adults; and the poverty runs carried no tenure, so every unit
was held to the renter line.
"""
from __future__ import annotations

from typing import Literal

from tax_modeler.errors import ConfigError

Tenure = Literal["renter", "owner_with_mortgage", "owner_no_mortgage"]
Area = Literal["honolulu", "kahului", "nonmetro"]

# BLS research SPM thresholds, two adults and two children, by tenure.
_BASE_THRESHOLD_2A2C: dict[int, dict[Tenure, float]] = {
    2021: {"owner_with_mortgage": 30_983.24, "owner_no_mortgage": 26_055.40, "renter": 31_216.69},
    2022: {"owner_with_mortgage": 33_978.49, "owner_no_mortgage": 28_454.92, "renter": 34_140.06},
    2023: {"owner_with_mortgage": 36_966.24, "owner_no_mortgage": 30_587.73, "renter": 37_230.75},
    2024: {"owner_with_mortgage": 39_230.99, "owner_no_mortgage": 32_878.59, "renter": 39_219.89},
    2025: {"owner_with_mortgage": 41_322.71, "owner_no_mortgage": 34_326.00, "renter": 41_700.56},
}

# Housing (shelter and utilities) share of each tenure's threshold, the share the
# rent index applies to: the values that reproduce Census's 2025 Hawaii thresholds.
_HOUSING_SHARE_BY_TENURE: dict[Tenure, float] = {
    "owner_with_mortgage": 0.434,
    "owner_no_mortgage": 0.323,
    "renter": 0.443,
}

# Census's Median Rent Index for Hawaii's SPM areas, 2025 (P60-290 Table 19).
_MEDIAN_RENT_INDEX: dict[Area, float] = {
    "honolulu": 1.493,   # Urban Honolulu MSA (46520)
    "kahului": 1.455,    # Kahului-Wailuku MSA (27980)
    "nonmetro": 1.106,   # Hawaii nonmetropolitan
}

# County (the population's ``county`` labels) -> SPM area.
COUNTY_AREA: dict[str, Area] = {
    "Honolulu": "honolulu",
    "Maui": "kahului",
    "Hawaii": "nonmetro",
    "Kauai": "nonmetro",
    "Kalawao": "nonmetro",
}

# Where a unit has no county: Urban Honolulu, home to about 70% of Hawaii's people.
DEFAULT_AREA: Area = "honolulu"

# PUMS TEN -> SPM tenure. 4 (occupied without payment of rent) takes the
# owner-without-mortgage threshold, as Census assigns it.
TEN_TO_TENURE: dict[int, Tenure] = {
    1: "owner_with_mortgage",
    2: "owner_no_mortgage",
    3: "renter",
    4: "owner_no_mortgage",
}


def _base_threshold_2a2c(year: int, tenure: Tenure = "renter", *, extrapolate: bool = False) -> float:
    """BLS's two-adult, two-child base threshold for ``year`` and ``tenure``.

    Tabled years return the table value. With ``extrapolate=True``, a year
    past the last tabled year indexes that year's value forward by
    ``CREDIT_PARAM_CPI_GROWTH``/yr, compounded: the chained-CPI assumption
    the projector and ``compute_base_tax`` use to extrapolate federal credit
    and bracket parameters past the last Rev. Proc., so a projected-year
    poverty run prices its threshold in the same dollars as its credits.
    The published SPM thresholds have grown faster than chained CPI
    (renters, 2021-2025: +7.5%/yr), so the extrapolated threshold is if
    anything low. Anything else raises ``ConfigError``; nothing extrapolates
    unless the caller asks.
    """
    if tenure not in _HOUSING_SHARE_BY_TENURE:
        raise ConfigError(f"Unknown tenure={tenure!r}", available=sorted(_HOUSING_SHARE_BY_TENURE))
    if year in _BASE_THRESHOLD_2A2C:
        return float(_BASE_THRESHOLD_2A2C[year][tenure])
    last = max(_BASE_THRESHOLD_2A2C)
    if extrapolate and year > last:
        from tax_modeler.credits.eitc import CREDIT_PARAM_CPI_GROWTH

        return float(_BASE_THRESHOLD_2A2C[last][tenure] * (1.0 + CREDIT_PARAM_CPI_GROWTH) ** (year - last))
    raise ConfigError(
        f"No Hawaii SPM threshold available for year={year}"
        + ("" if extrapolate else " (pass extrapolate=True for later years)"),
        available=sorted(_BASE_THRESHOLD_2A2C),
    )


def _geo_multiplier(tenure: Tenure, area: Area) -> float:
    """The rent adjustment on the whole threshold: ``(1 - share) + share * MRI``.

    For Urban Honolulu (MRI 1.493): renters 1.218, owners with a mortgage 1.214,
    owners without one 1.159.
    """
    if area not in _MEDIAN_RENT_INDEX:
        raise ConfigError(f"Unknown SPM area={area!r}", available=sorted(_MEDIAN_RENT_INDEX))
    share = _HOUSING_SHARE_BY_TENURE[tenure]
    return (1.0 - share) + share * _MEDIAN_RENT_INDEX[area]


def _equivalence_scale(n_adults: int, n_children: int) -> float:
    """SPM three-parameter equivalence scale, normalized to (2A, 2C).

    Census SPM Technical Documentation §4.2.1:

      * One and two adults (no children): scale = (adults)^0.5, set to 1.41
        for two adults
      * Single parents (1 adult + N children):
            scale = (1 + 0.8 + 0.5*(N-1))^0.7   for N >= 1
      * All other families, including three or more adults without children:
            scale = (adults + 0.5*children)^0.7

    The reference family (2 adults + 2 children) has raw scale
    ``(2 + 0.5*2)^0.7 = 3^0.7 ≈ 2.158``; returned scale is normalized
    by that reference so the 2A2C family always evaluates to 1.0.
    """
    if n_adults < 1:
        raise ConfigError("n_adults must be >= 1")
    if n_children < 0:
        raise ConfigError("n_children must be >= 0")

    if n_children == 0 and n_adults <= 2:
        raw = 1.0 if n_adults == 1 else 1.41
    elif n_adults == 1:
        # Single-parent families: first child weighted 0.8, others 0.5.
        raw = (1.0 + 0.8 + 0.5 * (n_children - 1)) ** 0.7
    else:
        raw = (n_adults + 0.5 * n_children) ** 0.7

    base = (2 + 0.5 * 2) ** 0.7  # = 3 ** 0.7
    return raw / base


def threshold_for_units(
    units,
    *,
    year: int = 2024,
    tenure_col: str = "tenure",
    county_col: str = "county",
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

    The tenure comes from ``tenure_col`` and the SPM area from ``county_col``
    (:data:`COUNTY_AREA`). A unit with no tenure is held to the renter line;
    one with no county, or a frame without the column, to Urban Honolulu's
    (:data:`DEFAULT_AREA`).

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
    if county_col in units.columns:
        areas = units[county_col].map(COUNTY_AREA).fillna(DEFAULT_AREA).to_numpy()
    else:
        areas = np.array([DEFAULT_AREA] * len(units))

    out = np.zeros(len(units))
    cache: dict[tuple, float] = {}
    for i in range(len(units)):
        ten = tenures[i] if tenures[i] in _HOUSING_SHARE_BY_TENURE else "renter"
        key = (int(n_adults[i]), int(n_children[i]), ten, areas[i])
        if key not in cache:
            cache[key] = hawaii_spm_threshold(
                year, n_adults=key[0], n_children=key[1], tenure=ten,  # type: ignore[arg-type]
                area=key[3], extrapolate=extrapolate,
            )
        out[i] = cache[key]
    return pd.Series(out, index=units.index)


def hawaii_spm_threshold(
    year: int,
    *,
    n_adults: int = 2,
    n_children: int = 2,
    tenure: Tenure = "renter",
    area: Area = DEFAULT_AREA,
    extrapolate: bool = False,
) -> float:
    """Hawaii SPM threshold for a unit of the given composition, tenure and area.

    Returns annual dollar threshold below which the household is in SPM
    poverty.

    Parameters
    ----------
    year:
        Calendar year. Must be in the available table unless
        ``extrapolate`` is set.
    n_adults, n_children:
        Household composition. Defaults match the Census base reference
        unit (2 adults + 2 children).
    tenure:
        ``"renter"``, ``"owner_with_mortgage"``, or ``"owner_no_mortgage"``.
    area:
        ``"honolulu"``, ``"kahului"`` or ``"nonmetro"`` (:data:`COUNTY_AREA`).
    extrapolate:
        For a year past the table, index the latest tabled year forward by
        the credit-parameter CPI assumption (see
        :func:`_base_threshold_2a2c`) instead of raising. Off by default.
    """
    base = _base_threshold_2a2c(year, tenure, extrapolate=extrapolate)
    return float(base * _geo_multiplier(tenure, area) * _equivalence_scale(n_adults, n_children))


def spm_threshold_table(
    year: int, *, area: Area = DEFAULT_AREA, extrapolate: bool = False,
) -> dict[tuple[int, int, Tenure], float]:
    """Pre-computed (n_adults, n_children, tenure) → threshold dict for one area.

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
                    area=area, extrapolate=extrapolate,
                )
    return out


def renter_housing_portion(
    year: int, *, n_adults: int, n_children: int, area: Area = DEFAULT_AREA, extrapolate: bool = False,
) -> float:
    """The shelter-and-utilities part of a renter unit's SPM threshold: the threshold times
    ``share * MRI / ((1 - share) + share * MRI)``, about 54% in Urban Honolulu. Census caps the value of a
    housing subsidy at this amount less the tenant's payment (SPM Technical Documentation)."""
    share = _HOUSING_SHARE_BY_TENURE["renter"]
    mri = _MEDIAN_RENT_INDEX[area]
    whole = hawaii_spm_threshold(year, n_adults=n_adults, n_children=n_children, tenure="renter", area=area,
                                 extrapolate=extrapolate)
    return whole * share * mri / _geo_multiplier("renter", area)
