"""Option bundles for projecting tax units to a target year.

``project_and_recalibrate`` (``calibration.year_recalibrator``),
``build_targets`` (``calibration.forward_targets``) and
``project_tax_units_forward`` (``projection.tax_unit_projector``) used to take
each of these settings as its own parameter, up to 22 at once. Each bundle is
one assumption; where a function can do without it, passing ``None`` turns
that step off (it used to be a separate ``use_*`` / ``apply_*`` flag).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CboAging:
    """Per-component income aging at CBO Outlook nominal rates (wages, capital
    gains, business income, ... each at its own rate) instead of uniform
    county-median growth (``calibration.cbo_aging``).

    vintage:
        CBO Outlook publication.
    hawaii_factors:
        Per-component multipliers on the national rates. None uses
        ``cbo_aging.DEFAULT_HAWAII_FACTORS`` (Hawaii wage growth ~0.85 of
        national, capital gains at parity); ``{c: 1.0 ...}`` is pure
        CBO-national aging, ITEP's method by construction.
    """

    vintage: str = "2025-01"
    hawaii_factors: dict[str, float] | None = None


@dataclass(frozen=True)
class TopPremium:
    """Extra annual income growth for top earners
    (``behavioral_response.apply_top_income_growth_premium``).

    annual:
        Premium per year (0.010 = +1.0%/yr, the MID scenario's).
    base_year:
        Year the premium compounds from (the most recent ACS).
    threshold:
        Minimum AGI the premium applies to.
    """

    annual: float = 0.010
    base_year: int = 2024
    threshold: float = 500_000.0


@dataclass(frozen=True)
class GrowthAssumptions:
    """How ``forward_targets.build_targets`` grows DOTAX's base-year filer
    counts and tax to the target year.

    cor_projections_M:
        Council on Revenues individual income tax projections ($M by fiscal
        year). None uses ``DEFAULT_COR_IIT_PROJECTIONS_M``.
    low_growth:
        Annual income growth for brackets below $200K.
    top_differential:
        Extra annual growth for brackets at or above $200K, compounded with
        ``low_growth`` (the top-1% empirical premium).
    rate_drift:
        Per-year effective-rate uplift from bracket creep as nominal incomes rise.
    population_growth_rate:
        Annual filer-count growth, applied uniformly after bracket migration.
        0.005 sits between DBEDT's ~0.34%/yr population and ~0.6%/yr labor-force
        projections (2050 series, April 2024).
    """

    cor_projections_M: dict[int, float] | None = None
    low_growth: float = 0.025
    top_differential: float = 0.010
    rate_drift: float = 0.005
    population_growth_rate: float = 0.005


@dataclass(frozen=True)
class SoiAnchor:
    """Re-synthesize the $1M+ filers from national IRS SOI Table 1.4 tiers
    (``calibration.soi_top_anchor``) at the forward target count.

    year:
        SOI Table 1.4 vintage.
    hawaii_capgain_adjustment:
        Multiplier on the national SOI capital-gains share for Hawaii residents
        (DOTAX Table 21 evidence).
    """

    year: int = 2022
    hawaii_capgain_adjustment: float = 0.95
