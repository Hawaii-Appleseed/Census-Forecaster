"""Tests for tax_modeler.benefits._fpl multi-year FPL guidelines."""
from __future__ import annotations

import pytest
from tax_modeler.benefits._fpl import EARLIEST_FPL_YEAR, fpl_year_for, hawaii_fpl
from tax_modeler.errors import ConfigError


def test_fpl_2024_published_table():
    """2024 HHS Hawaii guidelines, unchanged from the published table."""
    assert hawaii_fpl(2024, household_size=1) == pytest.approx(16_770.0)
    assert hawaii_fpl(2024, household_size=3) == pytest.approx(28_590.0)


def test_fpl_2025_published_table():
    """2025 HHS Hawaii guidelines (Federal Register Jan 2025)."""
    assert hawaii_fpl(2025, household_size=1) == pytest.approx(17_310.0)
    assert hawaii_fpl(2025, household_size=3) == pytest.approx(29_570.0)


def test_fpl_2028_projected_above_2025():
    """2028 is projected off the 2025 base by the CPI inflator (>1)."""
    base = hawaii_fpl(2025, household_size=3)
    proj = hawaii_fpl(2028, household_size=3)
    assert proj > base
    assert proj == pytest.approx(base * 1.071)


def test_fpl_extends_past_size_8():
    """Sizes above 8 add the per-extra-person increment."""
    # 2025: size 8 = 60,220, increment = 6,130 → size 10 = 72,480
    assert hawaii_fpl(2025, household_size=10) == pytest.approx(72_480.0)


def test_fpl_unsupported_year_raises():
    with pytest.raises(ConfigError):
        hawaii_fpl(2030, household_size=2)


def test_fpl_bad_household_size_raises():
    with pytest.raises(ConfigError):
        hawaii_fpl(2025, household_size=0)


def test_fpl_year_for_clamps_pre_2024_to_earliest():
    """Benefit modules with pre-2024 rate tables (e.g. school lunch) fall back
    to the earliest FPL table, since HHS guidelines only begin at 2024."""
    assert EARLIEST_FPL_YEAR == 2024
    assert fpl_year_for(2022) == 2024
    assert fpl_year_for(2023) == 2024


def test_fpl_year_for_passes_through_supported_and_forward_years():
    assert fpl_year_for(2024) == 2024
    assert fpl_year_for(2025) == 2025
    assert fpl_year_for(2028) == 2028
