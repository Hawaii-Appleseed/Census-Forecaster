"""Hawaii Federal Poverty Level (FPL) guidelines.

HHS publishes annual poverty guidelines separately for the contiguous
48 states + DC, Alaska, and Hawaii. Hawaii is ~11-12% above contiguous
US. The tables below are the published HHS guidelines (Federal Register).
Years HHS has not yet published are projected forward off the latest
published table using a documented CPI-U inflator (see
``_FPL_PROJECTION_INFLATOR``). Update annually when HHS publishes a new
table — promote the projected year to a real ``_HI_FPL_BASE_<year>`` table.

Used by SNAP gross/net income tests (Phase 3, separate USDA tables),
Medicaid eligibility (Phase 5), WIC eligibility (Phase 5), ACA Premium
Tax Credit (Phase 5), and the hypothetical RxKids program (300% FPL
eligibility test).
"""
from __future__ import annotations

from tax_modeler.errors import ConfigError

# 2024 HHS Hawaii poverty guidelines (annual, by household size).
# Source: Federal Register, Jan 2024.
_HI_FPL_BASE_2024 = {
    1: 16_770, 2: 22_680, 3: 28_590, 4: 34_500,
    5: 40_410, 6: 46_320, 7: 52_230, 8: 58_140,
}
_HI_FPL_INCREMENT_PER_EXTRA_PERSON_2024 = 5_910

# 2025 HHS Hawaii poverty guidelines (annual, by household size).
# Source: Federal Register, Jan 2025 (90 FR 3856).
_HI_FPL_BASE_2025 = {
    1: 17_310, 2: 23_440, 3: 29_570, 4: 35_700,
    5: 41_830, 6: 47_960, 7: 54_090, 8: 60_220,
}
_HI_FPL_INCREMENT_PER_EXTRA_PERSON_2025 = 6_130

# Projected forward-year inflators applied to the 2025 published base for
# years HHS has not yet published. The HHS poverty guidelines are updated
# annually by the change in the Consumer Price Index (CPI-U). CBO's Jan
# 2025 Economic Outlook projects CPI-U growth of ~2.3%/yr for 2026-2028;
# we compound that off the 2025 base. These are estimates — when HHS
# publishes a real table for one of these years, replace the inflator with
# a ``_HI_FPL_BASE_<year>`` table.
_FPL_PROJECTION_INFLATOR = {
    2026: 1.023,   # 2025 base × (1.023)^1
    2027: 1.047,   # 2025 base × (1.023)^2
    2028: 1.071,   # 2025 base × (1.023)^3
}

# Years for which hawaii_fpl can return a guideline (published + projected).
_SUPPORTED_FPL_YEARS = (2024, 2025, 2026, 2027, 2028)


def _lookup(base: dict[int, int], increment: int, household_size: int) -> float:
    """Annual guideline from a base table, extending past size 8 linearly."""
    max_tabulated = max(base)
    if household_size <= max_tabulated:
        return float(base[household_size])
    extra = household_size - max_tabulated
    return float(base[max_tabulated] + extra * increment)


def hawaii_fpl(year: int = 2024, *, household_size: int = 1) -> float:
    """Annual HHS Hawaii poverty guideline for a household of ``household_size``.

    Supports published tables (2024, 2025) and CPI-projected forward years
    (2026-2028, off the 2025 base — see ``_FPL_PROJECTION_INFLATOR``).
    Raises ``ConfigError`` for any other year.
    """
    if household_size <= 0:
        raise ConfigError("household_size must be >= 1")
    if year == 2024:
        return _lookup(
            _HI_FPL_BASE_2024, _HI_FPL_INCREMENT_PER_EXTRA_PERSON_2024, household_size
        )
    if year == 2025:
        return _lookup(
            _HI_FPL_BASE_2025, _HI_FPL_INCREMENT_PER_EXTRA_PERSON_2025, household_size
        )
    if year in _FPL_PROJECTION_INFLATOR:
        base_2025 = _lookup(
            _HI_FPL_BASE_2025, _HI_FPL_INCREMENT_PER_EXTRA_PERSON_2025, household_size
        )
        return base_2025 * _FPL_PROJECTION_INFLATOR[year]
    raise ConfigError(
        f"hawaii_fpl: year {year} not supported",
        available=list(_SUPPORTED_FPL_YEARS),
    )


# Earliest year with a published/projected FPL table.
EARLIEST_FPL_YEAR = min(_SUPPORTED_FPL_YEARS)


def fpl_year_for(tax_year: int) -> int:
    """Map a run's tax year to the FPL table year used for income tests.

    Benefit modules thread the run's ``tax_year`` into their FPL test so
    forward-projected runs (e.g. TY2028) check eligibility against same-year
    thresholds. Some modules' own rate tables, however, predate the FPL
    series — school lunch carries 2022-2023 reimbursement rates, while the
    HHS FPL guidelines only begin at 2024. For those pre-2024 years there is
    no FPL table, so fall back to the earliest one (identical to the prior
    frozen-2024 behavior). Forward years are passed through unchanged and
    raise via :func:`hawaii_fpl` only if beyond the supported horizon.
    """
    return max(int(tax_year), EARLIEST_FPL_YEAR)


__all__ = ["hawaii_fpl", "fpl_year_for", "EARLIEST_FPL_YEAR"]
