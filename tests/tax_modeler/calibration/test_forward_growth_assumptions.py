"""The forward-target growth constants come from the bundled data file with
vintage metadata; the literals in ``forward_targets`` are a fallback pinned
to it."""
from __future__ import annotations

import json
import warnings

import pytest

from tax_modeler.calibration import forward_targets as ft


def test_file_values_and_fallback_literals_agree():
    d = ft.load_growth_assumptions()
    assert d["vintage"] != "fallback"
    for name, literal in ft._GROWTH_FALLBACK.items():
        assert d["assumptions"][name]["value"] == pytest.approx(literal), name
        assert d["assumptions"][name]["source"], f"{name} has no source"
        lo, hi = ft.growth_sensitivity(name)
        assert lo <= ft.growth_value(name) <= hi


def test_build_targets_defaults_read_the_file():
    a = ft.build_targets(2027, include_agi_targets=False, include_age_targets=False)
    b = ft.build_targets(
        2027, include_agi_targets=False, include_age_targets=False,
        low_growth=ft.growth_value("low_growth"),
        top_differential=ft.growth_value("top_differential"),
        rate_drift=ft.growth_value("rate_drift"),
        population_growth_rate=ft.growth_value("population_growth_rate"),
    )
    assert a.filer_targets == b.filer_targets
    assert a.tax_targets == pytest.approx(b.tax_targets)
    c = ft.build_targets(2027, include_agi_targets=False, include_age_targets=False,
                         population_growth_rate=0.0)
    assert sum(c.filer_targets.values()) < sum(a.filer_targets.values())


def test_cor_extrapolation_uses_the_file_rate_and_an_override():
    table = {2027: 1000.0}
    g = ft.growth_value("cor_extrapolation_growth")
    assert ft._back_cast_cor(2029, table) == pytest.approx(1000.0 * (1 + g) ** 2)
    assert ft._back_cast_cor(2025, table) == pytest.approx(1000.0 / (1 + g) ** 2)
    assert ft._back_cast_cor(2029, table, growth=0.0) == pytest.approx(1000.0)
    assert ft._back_cast_cor(2027, table, growth=0.9) == 1000.0


def test_missing_file_falls_back_with_a_warning(tmp_path):
    with pytest.warns(RuntimeWarning):
        d = ft.load_growth_assumptions(tmp_path / "nope.json")
    assert d["vintage"] == "fallback"
    assert d["assumptions"]["low_growth"]["value"] == ft._GROWTH_FALLBACK["low_growth"]
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"vintage": "x", "assumptions": {"low_growth": {"value": 1}}}))
    with pytest.warns(RuntimeWarning):
        assert ft.load_growth_assumptions(bad)["vintage"] == "fallback"
