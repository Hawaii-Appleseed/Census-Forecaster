"""Phase 11 smoke tests: scenario serialization + composition + typed params."""
from __future__ import annotations

from pathlib import Path

import pytest
from tax_modeler import (
    TAX_SYSTEM_FACTORY_REGISTRY,
    BehavioralParams,
    ForecastScenario,
    MacroScenario,
    Reform,
    TaxSystemRegistry,
    TopIncomeParams,
    apply_reform,
    register_tax_system,
)
from tax_modeler.errors import ConfigError

pytestmark = pytest.mark.smoke

REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------------------
# Typed scenario-parameter dataclasses
# ---------------------------------------------------------------------------


def test_top_income_params_presets_valid():
    for s in (TopIncomeParams.low(), TopIncomeParams.mid(), TopIncomeParams.high()):
        assert 1.0 < s.pareto_alpha < 2.0
        assert 0.0 <= s.top_premium_pct <= 0.05


def test_macro_scenario_presets_valid():
    for s in (MacroScenario.low(), MacroScenario.mid(), MacroScenario.high()):
        assert s.reec_demand_scenario in {"pre_obbba", "obbba_mid", "obbba_severe"}
        assert s.cor_scale_factor > 0


def test_forecast_scenario_bundle():
    s = ForecastScenario.mid()
    assert s.label == "MID"
    assert isinstance(s.top, TopIncomeParams)
    assert isinstance(s.behavior, BehavioralParams)
    assert isinstance(s.macro, MacroScenario)
    assert s.top.pareto_alpha == 1.5      # MID Pareto α
    assert s.behavior.eti == 0.40         # MID ETI


def test_forecast_scenario_named():
    assert ForecastScenario.named("low").label == "LOW"
    assert ForecastScenario.named("MID").label == "MID"
    assert ForecastScenario.named("static").label == "STATIC"
    with pytest.raises(ConfigError):
        ForecastScenario.named("very_high")


# ---------------------------------------------------------------------------
# Reform.to_dict / from_dict round-trip
# ---------------------------------------------------------------------------


def test_reform_to_dict_minimal():
    r = Reform(name="snap_test",
               benefit_overrides={"snap": {"max_benefit_pct": 0.9}})
    d = r.to_dict()
    assert d["name"] == "snap_test"
    assert "tax_system" not in d   # no tax change → key omitted
    assert d["benefit_overrides"] == {"snap": {"max_benefit_pct": 0.9}}


def test_reform_to_dict_with_tax_system():
    r = Reform(
        name="sb3125_cd1",
        tax_system_factory=TaxSystemRegistry.get_sb3125_cd1_system,
    )
    d = r.to_dict()
    assert d["tax_system"] == "sb3125_cd1"


def test_reform_round_trip_dict():
    r = Reform(
        name="combined",
        tax_system_factory=TaxSystemRegistry.get_sb3125_cd1_system,
        benefit_overrides={"snap": {"max_benefit_pct": 0.9},
                           "hi_eitc": {"rate_of_federal": 0.8}},
        metadata={"author": "dtomkatsu", "year": 2026},
    )
    d = r.to_dict()
    r2 = Reform.from_dict(d)
    assert r2.name == r.name
    assert r2.tax_system_factory == r.tax_system_factory  # bound-method ==
    assert dict(r2.benefit_overrides) == {
        "snap": {"max_benefit_pct": 0.9},
        "hi_eitc": {"rate_of_federal": 0.8},
    }
    assert dict(r2.metadata) == {"author": "dtomkatsu", "year": 2026}


def test_reform_from_dict_unknown_tax_system_raises():
    with pytest.raises(ConfigError):
        Reform.from_dict({"name": "bad", "tax_system": "made_up_bill"})


def test_reform_from_dict_missing_name_raises():
    with pytest.raises(ConfigError):
        Reform.from_dict({"benefit_overrides": {"snap": {}}})


# ---------------------------------------------------------------------------
# YAML round-trip
# ---------------------------------------------------------------------------


def test_reform_to_yaml_round_trip(tmp_path):
    r = Reform(
        name="snap_minus_20pct",
        benefit_overrides={"snap": {"max_benefit_pct": 0.8}},
        metadata={"author": "test"},
    )
    p = tmp_path / "reform.yaml"
    r.to_yaml(p)
    assert p.exists()
    r2 = Reform.from_yaml(p)
    assert r2.name == r.name
    assert dict(r2.benefit_overrides) == {"snap": {"max_benefit_pct": 0.8}}


def test_bundled_yamls_load_and_apply(taxed_units):
    """Every YAML in /reforms/ loads and runs through apply_reform."""
    reforms_dir = REPO_ROOT / "reforms"
    yamls = list(reforms_dir.glob("*.yaml"))
    assert len(yamls) >= 3, f"expected ≥3 example YAMLs, found {len(yamls)}"
    for y in yamls:
        reform = Reform.from_yaml(y)
        result = apply_reform(taxed_units, reform, year=2027)
        assert result is not None


# ---------------------------------------------------------------------------
# Reform.compose
# ---------------------------------------------------------------------------


def test_reform_compose_benefit_only():
    snap = Reform(
        name="snap_cut",
        benefit_overrides={"snap": {"max_benefit_pct": 0.9}},
    )
    eitc = Reform(
        name="eitc_x2",
        benefit_overrides={"eitc": {"amount_pct": 2.0}},
    )
    composed = Reform.compose(snap, eitc)
    assert composed.name.startswith("composed:")
    assert "snap" in composed.benefit_overrides
    assert "eitc" in composed.benefit_overrides
    assert composed.metadata["composed_from"] == ["snap_cut", "eitc_x2"]


def test_reform_compose_tax_plus_benefit():
    sb3125 = Reform(
        name="sb3125_cd1",
        tax_system_factory=TaxSystemRegistry.get_sb3125_cd1_system,
    )
    snap = Reform(
        name="snap_cut",
        benefit_overrides={"snap": {"max_benefit_pct": 0.9}},
    )
    composed = Reform.compose(sb3125, snap, name="combined_test")
    assert composed.name == "combined_test"
    assert composed.tax_system_factory == TaxSystemRegistry.get_sb3125_cd1_system
    assert "snap" in composed.benefit_overrides


def test_reform_compose_last_write_wins_on_program():
    a = Reform(
        name="a",
        benefit_overrides={"snap": {"max_benefit_pct": 0.9}},
    )
    b = Reform(
        name="b",
        benefit_overrides={"snap": {"max_benefit_pct": 0.5}},
    )
    composed = Reform.compose(a, b)
    # b's snap params win
    assert composed.benefit_overrides["snap"]["max_benefit_pct"] == 0.5


def test_reform_compose_empty_raises():
    with pytest.raises(ConfigError):
        Reform.compose()


# ---------------------------------------------------------------------------
# Custom factory registration
# ---------------------------------------------------------------------------


def test_register_tax_system_extension_point():
    custom = lambda y: TaxSystemRegistry.get_act46_system(y)  # noqa: E731
    register_tax_system("my_custom_bill", custom)
    assert "my_custom_bill" in TAX_SYSTEM_FACTORY_REGISTRY
    # Round-trip through dict
    r = Reform(name="custom_test", tax_system_factory=custom)
    d = r.to_dict()
    assert d["tax_system"] == "my_custom_bill"
    # Cleanup
    del TAX_SYSTEM_FACTORY_REGISTRY["my_custom_bill"]
