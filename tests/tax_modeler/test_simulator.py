"""Tax simulator, Python side (TAX_SIMULATOR_SCOPE.md phases 1-2).

Specs, inline schedules, the generalized behavioral response, presets, and
the population / scoring / web modules on a small synthetic population. The
browser kernel's parity with the real population is tests/simulator/.
"""
from __future__ import annotations

import dataclasses
import gzip
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from tax_modeler.config.tax_system_config import (
    TaxCalculator,
    TaxSystemConfig,
    TaxSystemRegistry,
    compare_systems,
)
from tax_modeler.errors import ConfigError
from tax_modeler.reform import Reform, apply_reform
from tax_modeler.reform.income_tax_spec import (
    SPEC_STATUSES,
    IncomeTaxSpec,
    current_law_system,
    load_spec,
)
from tax_modeler.scenarios.behavioral_response import (
    BehavioralParams,
    apply_behavioral_response,
    apply_migration_response,
    estimate_pte_election_shift_M,
    top_rate_changes,
)
from tax_modeler.simulator.golden import edge_specs, invalid_specs, random_specs
from tax_modeler.simulator.population import Population, frame_for, unit_arrays
from tax_modeler.simulator.presets import current_law_vintages, presets
from tax_modeler.simulator.scenarios import SCENARIOS
from tax_modeler.simulator.score import score_spec, score_systems
from tax_modeler.simulator.systems import system_to_json
from tax_modeler.simulator.web import pack, web_meta, write_web_files

REPO = Path(__file__).resolve().parents[2]
YEARS = (2027, 2028, 2029, 2030, 2031)
SCHED = {"single": [[0, 1.4], [14_400, 2.5], [48_000, 7.2], [500_000, 14.0]],
         "head_of_household": {"scale": "single", "factor": 1.5},
         "married_filing_jointly": {"scale": "single", "factor": 2.0}}


@pytest.fixture(scope="module")
def calc():
    return TaxCalculator()


def spec(**kw):
    return IncomeTaxSpec.from_dict({"name": "t", "first_year": 2027, **kw})


# ---------------------------------------------------------------------------
# spec parsing
# ---------------------------------------------------------------------------

class TestSpecParsing:

    def test_derived_statuses_scale_floors(self):
        s = spec(income_tax={"brackets": SCHED})
        (start, b), = s.brackets
        assert start == 2027
        assert b["head_of_household"] == ((0, 1.4), (21_600, 2.5), (72_000, 7.2), (750_000, 14.0))
        assert b["married_filing_jointly"][-1] == (1_000_000, 14.0)

    @pytest.mark.parametrize("d", edge_specs() + random_specs(12), ids=lambda d: d["name"])
    def test_round_trip(self, d):
        s = IncomeTaxSpec.from_dict(d)
        assert IncomeTaxSpec.from_dict(s.to_dict()) == s
        assert IncomeTaxSpec.from_dict(json.loads(json.dumps(s.to_dict()))) == s

    @pytest.mark.parametrize("d", invalid_specs(), ids=lambda d: json.dumps(d)[:60])
    def test_rejects_invalid(self, d):
        with pytest.raises(ConfigError):
            IncomeTaxSpec.from_dict(d)

    def test_nothing_changed(self):
        s = spec()
        assert not s.changes_anything
        assert s.system_for(2029) == current_law_system(2029)

    def test_load_yaml_example(self):
        s = load_spec(REPO / "reforms/examples/top_rate_14.yaml")
        assert s.name == "top_rate_14" and len(s.brackets) == 2


# ---------------------------------------------------------------------------
# systems
# ---------------------------------------------------------------------------

class TestSystems:

    def test_current_law_before_first_year(self):
        s = IncomeTaxSpec.from_dict({"name": "t", "first_year": 2029,
                                     "income_tax": {"brackets": SCHED, "personal_exemption": 2_400}})
        assert s.system_for(2028) == current_law_system(2028)
        cfg = s.system_for(2029)
        assert cfg.personal_exemption == 2_400
        assert cfg.brackets["Single_Married_Separate"][-1] == (500_000, 14.0)
        assert cfg.bracket_scenario is None and cfg.credit_scenario is None

    def test_inline_schedule_scores_like_the_csv(self, calc):
        """A spec restating current law's vintages taxes every unit exactly as
        the CSV-backed system does."""
        s = spec(income_tax={"bracket_vintages": current_law_vintages(calc)})
        units = _units(200, seed=3)
        for y in YEARS:
            a = calc.unit_liabilities(units, current_law_system(y))["net"]
            b = calc.unit_liabilities(units, s.system_for(y))["net"]
            np.testing.assert_array_equal(a, b)
            for fs in ("single", "married_filing_jointly", "head_of_household",
                       "married_filing_separately"):
                pd.testing.assert_frame_equal(calc.brackets_for(current_law_system(y), fs)
                                              [["income_min", "rate", "rate_decimal"]],
                                              calc.brackets_for(s.system_for(y), fs)
                                              [["income_min", "rate", "rate_decimal"]],
                                              check_dtype=False)   # CSV floors load as int

    def test_married_separate_uses_single(self, calc):
        cfg = spec(income_tax={"brackets": SCHED}).system_for(2027)
        pd.testing.assert_frame_equal(calc.brackets_for(cfg, "married_filing_separately"),
                                      calc.brackets_for(cfg, "single"))

    def test_sub_one_percent_rates_are_percent(self, calc):
        """The old 'rate > 1 means percent' guess read 0.5% as 50%."""
        low = {fs: [[0, 0.5], [100_000, 0.9]] for fs in SPEC_STATUSES}
        cfg = spec(income_tax={"brackets": low}).system_for(2027)
        t = calc.calculate_tax(150_000, cfg, "single", num_exemptions=1, deduction_override=0)
        taxable = 150_000 - cfg.personal_exemption
        assert t["tax_liability"] == pytest.approx(100_000 * 0.005 + (taxable - 100_000) * 0.009)
        np.testing.assert_allclose(calc.brackets_for(cfg, "single")["rate_decimal"], [0.005, 0.009])

    def test_standard_deduction_override(self, calc):
        sd = {"single": 1_000, "head_of_household": 2_000, "married_filing_jointly": 3_000}
        cfg = spec(income_tax={"standard_deduction": sd}).system_for(2031)
        assert calc.standard_deduction_for(cfg, "married_filing_separately") == 1_000
        assert calc.standard_deduction_for(cfg, "married_filing_jointly") == 3_000
        # brackets still current law's TY2031 vintage
        pd.testing.assert_frame_equal(calc.brackets_for(cfg, "single"),
                                      calc.brackets_for(current_law_system(2031), "single"))

    def test_system_to_json_rejects_unmodeled_credits(self, calc):
        with pytest.raises(ValueError, match="credit_scenario"):
            system_to_json(TaxSystemRegistry.get_hb2306_hd1_system(2027), calc)


# ---------------------------------------------------------------------------
# Reform integration
# ---------------------------------------------------------------------------

class TestReform:

    def test_scored_against_act24(self, calc):
        r = Reform.from_dict({"name": "pe", "first_year": 2027,
                              "income_tax": {"personal_exemption": 2_400}})
        units = _units(300, seed=5)
        res = apply_reform(units, r, year=2027, calculator=calc)
        assert res.baseline_system.name == "sb3125_cd2_2027"
        assert res.revenue_delta_millions < 0
        direct = compare_systems(units, current_law_system(2027), r.tax_system_factory(2027), calc)
        assert res.revenue_delta_millions == pytest.approx(direct.iloc[2]["revenue_millions"])

    def test_round_trip_and_compose(self):
        r = Reform.from_dict({"name": "pe", "first_year": 2028,
                              "income_tax": {"personal_exemption": 2_400}})
        assert Reform.from_dict(r.to_dict()).income_tax == r.income_tax
        c = Reform.compose(Reform(name="other"), r)
        assert c.income_tax == r.income_tax
        assert c.baseline_factory(2028).name == "sb3125_cd2_2028"

    def test_not_both(self):
        with pytest.raises(ConfigError):
            Reform.from_dict({"name": "x", "tax_system": "act46", "first_year": 2027,
                              "income_tax": {}})

    def test_direct_construction_is_scored_like_from_dict(self, calc):
        s = IncomeTaxSpec.from_dict({"name": "pe", "first_year": 2027,
                                     "income_tax": {"personal_exemption": 2_400}})
        direct = Reform(name="pe", income_tax=s)
        loaded = Reform.from_dict(s.to_dict())
        assert direct.changes_taxes and direct.baseline_factory(2027).name == "sb3125_cd2_2027"
        units = _units(300, seed=5)
        a = apply_reform(units, direct, year=2027, calculator=calc).revenue_delta_millions
        b = apply_reform(units, loaded, year=2027, calculator=calc).revenue_delta_millions
        assert a == b < 0
        with pytest.raises(ConfigError):
            Reform(name="pe", income_tax=s, tax_system_factory=TaxSystemRegistry.get_act46_system)

    def test_composed_reform_round_trips(self):
        r = Reform.from_dict({"name": "t", "first_year": 2027, "income_tax": {"personal_exemption": 2_400}})
        c = Reform.compose(r, Reform(name="snap", benefit_overrides={"snap": {"x": 1}}))
        back = Reform.from_dict(c.to_dict())
        assert back.name == c.name == "composed:t+snap"
        assert back.income_tax == r.income_tax and back.changes_taxes

    def test_unknown_keys_rejected(self):
        with pytest.raises(ConfigError, match="behaviour"):
            Reform.from_dict({"name": "t", "first_year": 2027, "income_tax": {}, "behaviour": ["static"]})

    def test_act24_alias(self):
        assert Reform.from_dict({"name": "a", "tax_system": "act24"}).tax_system_factory(2027).name \
            == "sb3125_cd2_2027"


# ---------------------------------------------------------------------------
# behavioral response, generalized
# ---------------------------------------------------------------------------

def _units(n, seed=0, top=False):
    rng = np.random.default_rng(seed)
    statuses = np.array(["single", "married_filing_jointly", "head_of_household",
                         "married_filing_separately"])
    income = rng.lognormal(11, 1.2, n)
    if top:
        income[: n // 4] = rng.uniform(400_000, 5_000_000, n // 4)
    return pd.DataFrame({
        "income": income, "filing_status": statuses[rng.integers(0, 4, n)],
        "num_dependents": rng.integers(0, 4, n), "weight": rng.uniform(1, 50, n),
        "hi_itemized_deduction": rng.uniform(0, 1, n) * income * 0.2,
        "synthetic_cg_share": np.where(rng.random(n) < 0.2, rng.uniform(0, 0.8, n), 0.0),
    })


class TestBehavioralGeneralized:

    def test_act24_top_rate_changes_are_the_sb3125_constants(self, calc):
        for y in YEARS:
            ch = top_rate_changes(TaxSystemRegistry.get_act46_system(y),
                                  TaxSystemRegistry.get_sb3125_cd2_system(y), calc)
            assert {fs: round(pp, 12) for fs, (pp, _, _) in ch.items()} == {fs: 2.0 for fs in ch}
            assert {fs: floor for fs, (_, floor, _) in ch.items()} == {
                "single": 500_000, "married_filing_jointly": 1_000_000,
                "head_of_household": 750_000, "married_filing_separately": 500_000,
                "qualifying_widow": 1_000_000}

    @pytest.mark.parametrize("year", YEARS)
    def test_migration_matches_the_constant_form_for_act24(self, calc, year):
        units = _units(4_000, seed=year, top=True)
        p = BehavioralParams.mid()
        const = apply_migration_response(units, p, target_year=year)
        derived = apply_migration_response(
            units, p, target_year=year, baseline_cfg=TaxSystemRegistry.get_act46_system(year),
            scenario_cfg=TaxSystemRegistry.get_sb3125_cd2_system(year), calculator=calc)
        np.testing.assert_array_equal(const["weight"], derived["weight"])
        assert (const["weight"] < units["weight"]).any()

    def test_rate_cuts_do_not_migrate(self, calc):
        units = _units(2_000, seed=1, top=True)
        cut = {"single": [[0, 1.4], [500_000, 9.0]], "head_of_household": {"scale": "single", "factor": 1.5},
               "married_filing_jointly": {"scale": "single", "factor": 2.0}}
        out = apply_migration_response(units, BehavioralParams.high(), target_year=2029,
                                       baseline_cfg=current_law_system(2029),
                                       scenario_cfg=spec(income_tax={"brackets": cut}).system_for(2029),
                                       calculator=calc)
        np.testing.assert_array_equal(out["weight"], units["weight"])

    def test_high_new_top_bracket_moves_only_those_above_it(self, calc):
        vint = current_law_vintages(calc)
        for v in vint:
            for rows in v["brackets"].values():
                rows.append([rows[-1][0] + 1_000_000, 15.0])     # 15% from floor+$1M
        cfg = spec(income_tax={"bracket_vintages": vint}).system_for(2027)
        units = _units(4_000, seed=2, top=True)
        out = apply_migration_response(units, BehavioralParams.mid(), target_year=2027,
                                       baseline_cfg=current_law_system(2027), scenario_cfg=cfg,
                                       calculator=calc)
        moved = out["weight"].to_numpy() != units["weight"].to_numpy()
        floors = units["filing_status"].map({"single": 1_500_000, "married_filing_separately": 1_500_000,
                                             "head_of_household": 1_750_000,
                                             "married_filing_jointly": 2_000_000}).to_numpy()
        np.testing.assert_array_equal(moved, units["income"].to_numpy() >= floors)

    def test_pte_matches_the_constant_form_for_act24(self, calc):
        units = _units(3_000, seed=4, top=True)
        p = dataclasses.replace(BehavioralParams.mid(), pte_capture=0.5)
        const = estimate_pte_election_shift_M(units, p)
        derived = estimate_pte_election_shift_M(
            units, p, baseline_cfg=TaxSystemRegistry.get_act46_system(2027),
            scenario_cfg=TaxSystemRegistry.get_sb3125_cd2_system(2027), calculator=calc)
        for k in const:
            assert derived[k] == pytest.approx(const[k], rel=1e-12)

    def test_first_year_starts_the_phase_in(self, calc):
        units = _units(3_000, seed=6, top=True)
        cl_2029 = current_law_vintages(calc)[1]["brackets"]
        top = spec(first_year=2029, income_tax={"brackets": {
            fs: rows[:-1] + [[rows[-1][0], 14.0]] for fs, rows in cl_2029.items()}})
        p = BehavioralParams.mid()
        a, _ = apply_behavioral_response(units, p, target_year=2029, baseline_cfg=top.baseline_for(2029),
                                         scenario_cfg=top.system_for(2029), calculator=calc,
                                         bill_effective_year=2029)
        b, _ = apply_behavioral_response(units, p, target_year=2029, baseline_cfg=top.baseline_for(2029),
                                         scenario_cfg=top.system_for(2029), calculator=calc,
                                         bill_effective_year=2027)
        # year one of the phase-in loses less than year three
        assert (a["weight"] >= b["weight"]).all() and (a["weight"] > b["weight"]).any()

    def test_phase_in_starts_when_the_top_rate_rises(self, calc):
        # The same law, written from 2027 (current law until 2029) or from
        # 2029, loses the same filers in every year.
        cl = current_law_vintages(calc)
        top15 = {fs: rows[:-1] + [[rows[-1][0], 15.0]] for fs, rows in cl[1]["brackets"].items()}
        early = spec(name="early", first_year=2027,
                     income_tax={"bracket_vintages": [cl[0], {"from": 2029, "brackets": top15}]})
        late = spec(name="late", first_year=2029, income_tax={"brackets": top15})
        units = _units(3_000, seed=8, top=True)
        p = BehavioralParams.mid()
        path = {s_.name: {y: top_rate_changes(s_.baseline_for(y), s_.system_for(y), calc) for y in YEARS}
                for s_ in (early, late)}
        for y in (2029, 2030, 2031):
            w = [apply_migration_response(units, p, target_year=y, baseline_cfg=s_.baseline_for(y),
                                          scenario_cfg=s_.system_for(y), calculator=calc,
                                          top_rate_path=path[s_.name])["weight"] for s_ in (early, late)]
            np.testing.assert_array_equal(w[0], w[1])
        # and in 2029 it is year one: 0.10 x 2 points x 1/5
        out = apply_migration_response(units, p, target_year=2029, baseline_cfg=late.baseline_for(2029),
                                       scenario_cfg=late.system_for(2029), calculator=calc,
                                       top_rate_path=path[late.name])
        assert out["_migration_factor"].min() == pytest.approx(1 - p.migration_elast * 2 * 0.2)

    def test_a_later_increase_phases_in_from_its_own_year(self, calc):
        path = {2027: {"single": (1.0, 500_000, 500_000)}, 2028: {"single": (1.0, 500_000, 500_000)},
                2029: {"single": (3.0, 500_000, 500_000)}}
        from tax_modeler.scenarios.behavioral_response import _phased_rate_change
        # 1 point in its third year (3/5) plus 2 more in their first (1/5)
        assert _phased_rate_change(path, "single", 2029, 5) == pytest.approx(1 * 0.6 + 2 * 0.2)
        # a constant change is change x phase: the constant form
        assert _phased_rate_change({2027: {"single": (2.0, 0, 0)}}, "single", 2029, 5) == pytest.approx(2 * 0.6)

    def test_migration_never_takes_more_than_everyone(self, calc):
        vint = [{"from": v["from"], "brackets": {fs: rows[:-1] + [[rows[-1][0], 40.0]]
                                                  for fs, rows in v["brackets"].items()}}
                for v in current_law_vintages(calc)]
        s_ = spec(income_tax={"bracket_vintages": vint})
        units = _units(3_000, seed=9, top=True)
        # an elasticity large enough to take more than everyone (the value
        # used before September 27, 2026)
        strong = dataclasses.replace(BehavioralParams.high(), migration_elast=0.15)
        out = apply_migration_response(units, strong, target_year=2031,
                                       baseline_cfg=s_.baseline_for(2031), scenario_cfg=s_.system_for(2031),
                                       calculator=calc, bill_effective_year=2027)
        assert (out["weight"] >= 0).all()
        assert (out.loc[units["income"] >= 1_000_000, "weight"] == 0).all()

    def test_half_tier_starts_at_current_laws_top_floor(self, calc):
        flat = {fs: [[0, 14.0]] for fs in ("single", "head_of_household", "married_filing_jointly")}
        s_ = spec(income_tax={"brackets": flat})
        units = _units(4_000, seed=10, top=True)
        out = apply_migration_response(units, BehavioralParams.mid(), target_year=2027,
                                       baseline_cfg=s_.baseline_for(2027), scenario_cfg=s_.system_for(2027),
                                       calculator=calc)
        moved = out["weight"].to_numpy() != units["weight"].to_numpy()
        floors = units["filing_status"].map({"single": 500_000, "married_filing_separately": 500_000,
                                             "head_of_household": 750_000,
                                             "married_filing_jointly": 1_000_000}).to_numpy()
        np.testing.assert_array_equal(moved, units["income"].to_numpy() >= floors)

    def test_pte_needs_a_top_rate_increase(self, calc):
        units = _units(3_000, seed=4, top=True)
        p = dataclasses.replace(BehavioralParams.mid(), pte_capture=0.35)
        cut = spec(income_tax={"brackets": {"single": [[0, 1.4], [500_000, 12.0]],
                                            "head_of_household": {"scale": "single", "factor": 1.5},
                                            "married_filing_jointly": {"scale": "single", "factor": 2}}})
        sd = spec(income_tax={"standard_deduction": {"single": 20_000, "head_of_household": 30_000,
                                                     "married_filing_jointly": 40_000}})
        for s_ in (cut, sd):
            out = estimate_pte_election_shift_M(units, p, baseline_cfg=s_.baseline_for(2027),
                                                scenario_cfg=s_.system_for(2027), calculator=calc)
            assert out["pte_revenue_loss_$M"] == 0


# ---------------------------------------------------------------------------
# presets and scenarios
# ---------------------------------------------------------------------------

class TestPresets:

    @pytest.mark.parametrize("d", presets(), ids=lambda d: d["name"])
    def test_valid(self, d):
        IncomeTaxSpec.from_dict(d)

    def test_act46_preset_is_act46(self, calc):
        s = IncomeTaxSpec.from_dict(next(d for d in presets(calc) if d["name"] == "act46"))
        for y in YEARS:
            a, b = system_to_json(s.system_for(y), calc), system_to_json(
                TaxSystemRegistry.get_act46_system(y), calc)
            assert a["brackets"] == b["brackets"] and a["standard_deduction"] == b["standard_deduction"]

    def test_top_rate_14_changes_only_the_top_rate(self, calc):
        s = IncomeTaxSpec.from_dict(next(d for d in presets(calc) if d["name"] == "top_rate_14"))
        for y in YEARS:
            a, b = system_to_json(s.system_for(y), calc), system_to_json(current_law_system(y), calc)
            for fs in a["brackets"]:
                assert a["brackets"][fs][:-1] == b["brackets"][fs][:-1]
                assert a["brackets"][fs][-1] == [b["brackets"][fs][-1][0], 14.0]


def test_scenarios_match_the_act24_run():
    spec_ = importlib.util.spec_from_file_location("enh", REPO / "forecast_sb3125_enhanced.py")
    mod = importlib.util.module_from_spec(spec_)
    sys.modules["enh"] = mod
    spec_.loader.exec_module(mod)
    by_label = {s["label"]: s for s in mod.SCENARIOS}
    for key, sc in SCENARIOS.items():
        e = by_label[sc.label]
        assert (sc.alpha, sc.top_premium, sc.behavior) == (e["alpha"], e["top_premium"], e["behav"]), key
        assert e["macro_shock"] is None


# ---------------------------------------------------------------------------
# population, scoring, web files (synthetic population)
# ---------------------------------------------------------------------------

def _synthetic_population(n=120, years=(2027, 2028)) -> Population:
    rng = np.random.default_rng(9)
    hh = np.repeat(np.arange(n // 2), 2)
    a = {
        "filer_id": np.array([f"U{i}" for i in range(n)]),
        "hh_id": np.array([f"H{i}" for i in range(n // 2)]),
        "hh": hh.astype(np.int32),
        "fs": rng.integers(0, 4, n).astype(np.uint8),
        "deps": rng.integers(0, 3, n).astype(np.uint8),
        "weight": rng.uniform(1, 30, n),
        "cg": np.where(rng.random(n) < 0.2, 0.4, 0.0),
        "hh_weight": rng.uniform(5, 40, n // 2),
    }
    for y in years:
        a[f"agi_{y}"] = rng.lognormal(11, 1.3, n)
        a[f"item_{y}"] = a[f"agi_{y}"] * 0.1
        a[f"tci_{y}"] = a[f"agi_{y}"] * 1.05
        a[f"order_{y}"] = rng.permutation(n).astype(np.int32)
        a[f"hhfw_{y}"] = a["weight"][::2].copy()
        a[f"quint_{y}"] = rng.integers(0, 5, n // 2).astype(np.uint8)
        for k in ("low", "high"):
            idx = np.array([0, 5], dtype=np.int32)
            a[f"ovr_{k}_{y}_idx"] = idx
            a[f"ovr_{k}_{y}_agi"] = a[f"agi_{y}"][idx] * 1.1
            a[f"ovr_{k}_{y}_item"] = a[f"item_{y}"][idx]
    for k in ("low", "high"):
        a[f"tail_{k}_idx"] = np.array([7], dtype=np.int32)
        a[f"tail_{k}_weight"] = np.array([2.0])
        a[f"tail_{k}_cg"] = np.array([0.5])
    meta = {"years": list(years), "filing_statuses": ["single", "married_filing_jointly",
                                                      "head_of_household", "married_filing_separately"],
            "quintile_labels": ["Q1 (bottom 20%)", "Q2", "Q3", "Q4", "Q5 (top 20%)"],
            "quintile_breaks": [20_000, 45_000, 80_000, 140_000], "n_units": n,
            "n_households": n // 2, "scenarios": {}, "calibrated_base": {}}
    return Population(arrays=a, meta=meta)


class TestPopulationScoring:

    def test_overrides_apply(self):
        pop = _synthetic_population()
        mid, low = unit_arrays(pop, 2027, "mid"), unit_arrays(pop, 2027, "low")
        assert low["income"][5] == pytest.approx(mid["income"][5] * 1.1)
        assert low["weight"][7] == 2.0 and low["synthetic_cg_share"][7] == 0.5
        assert (low["income"][[1, 2, 3]] == mid["income"][[1, 2, 3]]).all()

    def test_frame_order(self):
        pop = _synthetic_population()
        f = frame_for(pop, 2028)
        np.testing.assert_array_equal(f["filer_id"], pop.arrays["filer_id"][pop.arrays["order_2028"]])

    def test_no_change_scores_zero(self):
        pop = _synthetic_population()
        res = score_spec(pop, spec(), distribution_years=[2027])
        for r in res["revenue"]:
            assert r["static_$M"] == 0 and r["behavioral_$M"] == 0

    def test_exemption_increase_costs_revenue(self):
        pop = _synthetic_population()
        res = score_spec(pop, spec(income_tax={"personal_exemption": 5_000}))
        assert all(r["static_$M"] < 0 for r in res["revenue"])

    def test_the_baseline_has_no_response(self, calc):
        # A 14% top rate: behavioral = the plan's tax on the population after
        # it responds minus current law's on the population as it was, so a
        # filer who moves away costs their whole tax.
        pop = _synthetic_population(years=YEARS)
        pop.arrays["agi_2029"][:20] = np.linspace(1.2e6, 9e6, 20)   # a top tail to respond
        vint = [{"from": v["from"], "brackets": {fs: rows[:-1] + [[rows[-1][0], 14.0]]
                                                  for fs, rows in v["brackets"].items()}}
                for v in current_law_vintages(calc)]
        s_ = spec(income_tax={"bracket_vintages": vint})
        r = score_spec(pop, s_, scenarios=["mid"], years=[2029])["revenue"][0]
        df = frame_for(pop, 2029, "mid")
        path = {y: top_rate_changes(s_.baseline_for(y), s_.system_for(y), calc) for y in YEARS}
        adj, _ = apply_behavioral_response(df, SCENARIOS["mid"].behavioral_params, target_year=2029,
                                           baseline_cfg=s_.baseline_for(2029),
                                           scenario_cfg=s_.system_for(2029), calculator=calc,
                                           top_rate_path=path)
        net = lambda frame, cfg: float((calc.unit_liabilities(frame, cfg)["net"]  # noqa: E731
                                        * frame["weight"].to_numpy()).sum()) / 1e6
        want = net(adj, s_.system_for(2029)) - net(df, s_.baseline_for(2029))
        assert r["behavioral_$M"] == pytest.approx(want, rel=1e-9, abs=1e-9)
        # below the pre-September 27, 2026 accounting, which scored both
        # systems on the responded population
        old = net(adj, s_.system_for(2029)) - net(adj, s_.baseline_for(2029))
        assert r["behavioral_$M"] < old

    def test_migrants_from_a_temporary_rise_stay_gone(self, calc):
        # 15% in 2027-28, then current law: from 2029 the plan taxes everyone
        # as current law does, but the rise's migration unwinds only as the
        # fall phases in, and those filers' tax is lost.
        pop = _synthetic_population(years=YEARS)
        for y in YEARS:
            pop.arrays[f"agi_{y}"][:20] = np.linspace(1.2e6, 9e6, 20)
        cl = current_law_vintages(calc)
        top15 = {fs: rows[:-1] + [[rows[-1][0], 15.0]] for fs, rows in cl[0]["brackets"].items()}
        s_ = spec(income_tax={"bracket_vintages": [{"from": 2027, "brackets": top15}, cl[1]]})
        for r in score_spec(pop, s_, scenarios=["mid"])["revenue"]:
            if r["tax_year"] >= 2029:
                assert r["static_$M"] == 0
                assert r["behavioral_$M"] < 0

    def test_migration_elasticities_are_shares_per_point(self):
        # The share of $1M+ filers who leave per point of top-rate increase.
        # The US evidence is ~0.001-0.01; 0.05-0.15 (in use until September
        # 27, 2026) read percent estimates as shares.
        e = [BehavioralParams.named(k).migration_elast for k in ("low", "mid", "high")]
        assert 0 < e[0] < e[1] < e[2] <= 0.02

    def test_distribution_names_the_baseline(self):
        pop = _synthetic_population()
        d = score_spec(pop, spec(income_tax={"personal_exemption": 5_000}), distribution_years=[2027])
        cols = set(d["distribution"][2027]["quintile"][0]) | set(d["distribution"][2027]["income_class"][0])
        assert not any("act46" in c or "cd1" in c for c in cols)
        assert {"total_baseline_$M", "total_reform_$M", "avg_baseline_tax"} <= cols

    def test_splitting_a_bracket_changes_no_one(self, calc):
        # same rate above and below the new floor: floating-point noise only
        pop = _synthetic_population()
        vint = [{"from": v["from"], "brackets": {fs: rows + [[rows[-1][0] * 1.5, rows[-1][1]]]
                                                  for fs, rows in v["brackets"].items()}}
                for v in current_law_vintages(calc)]
        d = score_spec(pop, spec(income_tax={"bracket_vintages": vint}), distribution_years=[2027])
        for kind in ("quintile", "income_class"):
            for g in d["distribution"][2027][kind]:
                assert g["pct_pay_more"] == 0 and g["pct_pay_less"] == 0

    def test_model_version_follows_the_content(self, tmp_path):
        pop = _synthetic_population(years=YEARS)
        pop.arrays["weight"][:3] = 0.0
        pop.arrays["agi_2027"][:3] = 2_000_000          # zero-weight $1M+ records
        versions = []
        for name, src in (("a", b"k1"), ("b", b"k1"), ("c", b"k2")):
            write_web_files(pop, tmp_path / name, kernel_source=src)
            m = json.loads((tmp_path / name / "population.json").read_text())
            versions.append(m["model_version"])
        assert versions[0] == versions[1] != versions[2]
        assert m["top_tail"]["records_1m"] == int(((pop.arrays["agi_2027"] >= 1e6)
                                                   & (pop.arrays["weight"] > 0)).sum())

    def test_save_load(self, tmp_path):
        pop = _synthetic_population()
        pop.save(tmp_path)
        back = Population.load(tmp_path)
        assert back.meta == pop.meta
        for k, v in pop.arrays.items():
            np.testing.assert_array_equal(back.arrays[k], v)

    def test_web_pack_round_trip(self, calc):
        pop = _synthetic_population()
        arrays = {"x": ("f8", np.array([1.5, -2.0])), "c": ("u1", np.array([3, 4, 5])),
                  "i": ("u4", np.array([7, 70000]))}
        blob, index = pack(arrays)
        for entry in index:
            assert entry["offset"] % 8 == 0
            dt = {"f8": "<f8", "u1": "u1", "u4": "<u4"}[entry["dtype"]]
            got = np.frombuffer(blob, dtype=dt, count=entry["length"], offset=entry["offset"])
            np.testing.assert_array_equal(got, arrays[entry["name"]][1])

    def test_custom_cli(self, tmp_path, monkeypatch):
        pop = _synthetic_population(years=YEARS)
        pop.save(tmp_path / "pop")
        (tmp_path / "s.json").write_text(json.dumps(
            {"name": "cli", "first_year": 2027, "income_tax": {"personal_exemption": 3_000}}))
        monkeypatch.syspath_prepend(str(REPO))
        spec_ = importlib.util.spec_from_file_location("forecast_custom", REPO / "forecast_custom.py")
        mod = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(mod)
        out = tmp_path / "out"
        assert mod.main(["--spec", str(tmp_path / "s.json"), "--population", str(tmp_path / "pop"),
                         "--out", str(out)]) == 0
        rev = pd.read_csv(out / "revenue.csv")
        assert len(rev) == 15 and (rev["static_$M"] < 0).all()
        assert (out / "quintile_2027.csv").exists() and (out / "manifest.json").exists()
        assert mod.main(["--spec", str(tmp_path / "s.json"), "--population", str(tmp_path / "none")]) == 1
        (tmp_path / "bad.json").write_text(json.dumps({"name": "Bad", "first_year": 2027}))
        assert mod.main(["--spec", str(tmp_path / "bad.json"), "--population", str(tmp_path / "pop")]) == 2
        # a missing file, broken YAML, a year the population lacks: messages, not tracebacks
        (tmp_path / "broken.yaml").write_text("first_year: [2027\n")
        for argv in (["--spec", str(tmp_path / "missing.yaml")],
                     ["--spec", str(tmp_path / "broken.yaml")],
                     ["--spec", str(tmp_path / "s.json"), "--distribution-year", "2026"]):
            assert mod.main(argv + ["--population", str(tmp_path / "pop"), "--out", str(tmp_path / "x")]) == 2

    def test_custom_cli_without_mid(self, tmp_path, monkeypatch, capsys):
        pop = _synthetic_population(years=YEARS)
        pop.save(tmp_path / "pop")
        (tmp_path / "s.json").write_text(json.dumps({"name": "lo", "first_year": 2027, "behavior": ["low"],
                                                     "income_tax": {"personal_exemption": 3_000}}))
        monkeypatch.syspath_prepend(str(REPO))
        spec_ = importlib.util.spec_from_file_location("forecast_custom", REPO / "forecast_custom.py")
        mod = importlib.util.module_from_spec(spec_)
        spec_.loader.exec_module(mod)
        assert mod.main(["--spec", str(tmp_path / "s.json"), "--population", str(tmp_path / "pop"),
                         "--out", str(tmp_path / "out")]) == 0
        printed = capsys.readouterr().out
        assert "MID static" not in printed and "nan" not in printed.lower()
