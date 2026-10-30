"""Tests for the data-driven scenario registry.

The load-bearing test is `test_registry_matches_direct_getter`: every spec
must resolve to exactly what the pre-registry scripts called directly, so
routing a runner through the registry cannot change any scenario's math.
"""

from __future__ import annotations

import dataclasses

import pytest
from tax_modeler.config.tax_system_config import TaxSystemRegistry
from tax_modeler.scenarios.registry import (
    OVERLAY_MODES,
    SCENARIO_SPECS,
    ScenarioSpec,
    baseline_for,
    get_scenario,
    list_scenarios,
)

# (slug, direct getter, year) — the exact calls the scripts made before.
DIRECT_EQUIVALENTS = [
    ("act46", lambda y: TaxSystemRegistry.get_act46_system(y), 2027),
    ("act46", lambda y: TaxSystemRegistry.get_act46_system(y), 2031),
    ("sb3125_cd1", lambda y: TaxSystemRegistry.get_sb3125_cd1_system(y), 2027),
    ("sb3125_cd1", lambda y: TaxSystemRegistry.get_sb3125_cd1_system(y), 2031),
    ("sb3125_cd2", lambda y: TaxSystemRegistry.get_sb3125_cd2_system(y), 2027),
    ("sb3125_sd1", lambda y: TaxSystemRegistry.get_sb3125_sd1_system(y), 2027),
    ("hb2306_hd1", lambda y: TaxSystemRegistry.get_hb2306_hd1_system(y), 2027),
    ("hb2306_hd1", lambda y: TaxSystemRegistry.get_hb2306_hd1_system(y), 2031),
    ("hb2306_orig", lambda y: TaxSystemRegistry.get_hb2306_orig_system(y), 2027),
    ("sb3125_original", lambda _y: TaxSystemRegistry.get_sb3125_original_2027_system(), 2027),
    ("millionaire_tax", lambda _y: TaxSystemRegistry.get_millionaire_tax_2027(), 2027),
]


def _fields(cfg):
    """Comparable snapshot of a TaxSystemConfig."""
    if dataclasses.is_dataclass(cfg):
        return dataclasses.asdict(cfg)
    return {
        k: getattr(cfg, k) for k in dir(cfg)
        if not k.startswith("_") and not callable(getattr(cfg, k))
    }


@pytest.mark.parametrize("slug,getter,year", DIRECT_EQUIVALENTS)
def test_registry_matches_direct_getter(slug, getter, year):
    """Registry resolution == the direct TaxSystemRegistry call."""
    assert _fields(get_scenario(slug).system_for(year)) == _fields(getter(year))


def test_all_specs_resolve_at_2027():
    for spec in list_scenarios():
        assert spec.system_for(2027) is not None


def test_multi_year_specs_resolve_across_horizon():
    for spec in list_scenarios():
        if not spec.takes_year:
            continue
        for year in (2027, 2028, 2029, 2030, 2031):
            assert spec.supports_year(year)
            assert spec.system_for(year) is not None


def test_fixed_year_specs_reject_other_years():
    spec = get_scenario("millionaire_tax")
    assert not spec.supports_year(2028)
    with pytest.raises(ValueError, match="only defined for TY2027"):
        spec.system_for(2028)


def test_baseline_resolution():
    assert baseline_for("sb3125_cd1").slug == "act46"
    assert baseline_for("hb2306_hd1").slug == "act46"
    assert baseline_for("act46") is None  # the baseline itself


def test_family_filter():
    slugs = {s.slug for s in list_scenarios(family="sb3125")}
    assert slugs == {"sb3125_cd1", "sb3125_cd2", "sb3125_sd1", "sb3125_original"}
    assert list_scenarios(family="nonexistent") == []


def test_unknown_slug_lists_known_ones():
    with pytest.raises(KeyError, match="sb3125_cd1"):
        get_scenario("no_such_bill")


def test_overlay_modes_valid_and_cd2_is_vintage():
    for spec in list_scenarios():
        assert spec.overlay in OVERLAY_MODES
    # CD2 adds vintage carryforward + dynamic AGI eligibility; CD1 does not.
    assert get_scenario("sb3125_cd2").overlay == "vintage"
    assert get_scenario("sb3125_cd1").overlay == "standard"
    # HB 2306 does not restrict REEC at all.
    assert get_scenario("hb2306_hd1").overlay == "none"


def test_spec_validation_rejects_bad_input():
    with pytest.raises(ValueError, match="overlay must be one of"):
        ScenarioSpec(slug="x", label="X", family="f",
                     system=lambda y: None, overlay="bogus")
    with pytest.raises(ValueError, match="must declare fixed_year"):
        ScenarioSpec(slug="x", label="X", family="f",
                     system=lambda: None, takes_year=False)


def test_slugs_match_dict_keys():
    for slug, spec in SCENARIO_SPECS.items():
        assert slug == spec.slug
