"""ACS supplement growth factors run from the PUMS dollar year, whatever the panel's latest vintage, and an observed
target year is read off the panel. Until 2026-11-22 the default start was the panel's latest year: the same year as the
PUMS (2024) until the 2025 vintage, which then moved every factor's start a year later."""
from __future__ import annotations

import pytest
from tax_modeler.projection import acs_supplement_forecast as supp


def _levels(geoid: str, indicator: str) -> dict[int, float]:
    return {o.year: o.estimate for o in supp._load_acs_series(geoid, indicator)}


def test_default_anchor_is_the_pums_dollar_year():
    from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR

    assert supp.DEFAULT_ANCHOR_YEAR == PUMS_INCOME_DOLLAR_YEAR
    s = supp.project_acs_supplement(PUMS_INCOME_DOLLAR_YEAR + 3, "15003")
    assert s.anchor_year == PUMS_INCOME_DOLLAR_YEAR


@pytest.mark.parametrize("indicator", ["B25077_001E", "B25064_001E"])
def test_observed_target_is_read_off_the_panel(indicator):
    levels = _levels("15003", indicator)
    y0, last = supp.DEFAULT_ANCHOR_YEAR, max(levels)
    if last <= y0:
        pytest.skip("the panel has no year after the dollar year")
    level, factor, anchor = supp._project_one_indicator("15003", indicator, last, None, None)
    assert anchor == y0
    assert level == levels[last]
    assert factor == pytest.approx(levels[last] / levels[y0])


def test_a_later_target_is_projected_and_divided_by_the_dollar_year():
    levels = _levels("15003", "B25077_001E")
    y0, last = supp.DEFAULT_ANCHOR_YEAR, max(levels)
    level, factor, anchor = supp._project_one_indicator("15003", "B25077_001E", last + 2, None, None)
    assert anchor == y0
    assert factor == pytest.approx(level / levels[y0])
