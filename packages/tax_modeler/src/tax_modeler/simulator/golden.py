"""Golden fixtures for the browser kernel's parity tests.

Every expected value comes from the real Python pipeline (:mod:`.score`,
``TaxCalculator``) on the same population the kernel loads, so
``tests/simulator/kernel.test.mjs`` checks the JavaScript against the model
itself, not against a second implementation.

Cases carry explicit per-year systems (:func:`.systems.system_to_json`), so
the kernel is tested on scoring alone; resolving a spec into systems is
checked separately (``spec_resolution``).

The kernel implements the statutory capital-gains alternative tax only, so
the registry systems (Act 24, Act 46), which default to the stacked
shortcut, enter the fixtures with ``cg_alt_tax="statute"`` (:func:`_statute`),
as the Act 24 pipeline and every spec score them. Every case is
scored on the simulator's DOTAX-anchored gains base.
"""
from __future__ import annotations

import dataclasses
import json
import random
from pathlib import Path

import numpy as np
import pandas as pd

from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemConfig, TaxSystemRegistry
from tax_modeler.errors import ConfigError
from tax_modeler.reform.income_tax_spec import CG_ORDINARY, IncomeTaxSpec, current_law_system
from tax_modeler.scenarios.behavioral_response import score_with_response, top_rate_path

from .population import Population, frame_for
from .presets import current_law_vintages, presets
from .scenarios import SCENARIOS
from .score import score_systems
from .systems import system_to_json

SAMPLE_UNITS = 400   # per-unit tax parity sample (plus every unit above $1M)
# the cases whose per-unit taxes are checked on the sample
UNIT_SAMPLE_CASES = ("act24_vs_act46", "top_rate_14", "cg_9", "cg_ordinary", "top14_cg9",
                     "cg9_high_floor")
# the case, scenario and year whose responded population is checked unit by
# unit (income, gains share, weight and net tax after ETI, migration and
# realization)
RESPONSE_SAMPLE = ("top14_cg9", "mid", 2031)


def _statute(factory):
    """A registry system factory with the statutory alternative tax (7.25%)."""
    return lambda year: dataclasses.replace(factory(year), cg_alt_tax="statute")


def _random_schedule(rng: random.Random) -> list[list[float]]:
    n = rng.randint(1, 14)
    floors = sorted(rng.sample(range(1_000, 2_000_000, 500), n - 1))
    rates = [round(rng.uniform(0, 16), rng.choice([0, 1, 2])) for _ in range(n)]
    if rng.random() < 0.7:
        rates.sort()
    return [[0.0, rates[0]]] + [[float(f), r] for f, r in zip(floors, rates[1:])]


def random_specs(n: int, seed: int = 20260926) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        it: dict = {}
        single = _random_schedule(rng)
        if rng.random() < 0.5:
            it["brackets"] = {"single": single,
                              "head_of_household": {"scale": "single", "factor": 1.5},
                              "married_filing_jointly": {"scale": "single", "factor": 2.0}}
        else:
            it["brackets"] = {"single": single, "head_of_household": _random_schedule(rng),
                              "married_filing_jointly": _random_schedule(rng)}
        if rng.random() < 0.4:
            base = rng.choice([0, 2_000, 8_000, 12_000, 30_000])
            it["standard_deduction"] = {"single": base, "head_of_household": base * 1.5,
                                        "married_filing_jointly": base * 2}
        if rng.random() < 0.4:
            it["personal_exemption"] = rng.choice([0, 600, 1_144, 2_400, 5_000])
        out.append({"name": f"random_{i}", "first_year": rng.choice([2027, 2027, 2028, 2030]),
                    "income_tax": it})
    return out


def random_cg_specs(n: int, seed: int = 20260927) -> list[dict]:
    """Random plans that change the capital-gains rate, alone (even ``i``) or
    with random brackets (odd ``i``). Their own seed, so :func:`random_specs`
    stays as it was."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        it: dict = {}
        if i % 2:
            single = _random_schedule(rng)
            it["brackets"] = {"single": single,
                              "head_of_household": {"scale": "single", "factor": 1.5},
                              "married_filing_jointly": {"scale": "single", "factor": 2.0}}
        rate = round(rng.uniform(0, 16), rng.choice([0, 1, 2]))
        it["capital_gains_rate"] = CG_ORDINARY if rng.random() < 0.25 else rate
        out.append({"name": f"random_cg_{i}", "first_year": rng.choice([2027, 2027, 2028, 2030]),
                    "income_tax": it})
    return out


def edge_specs() -> list[dict]:
    cl = current_law_vintages()
    low_rates = [{"from": v["from"], "brackets": {fs: [[f, r / 10] for f, r in rows]
                                                  for fs, rows in v["brackets"].items()}}
                 for v in cl]
    flat = {fs: [[0.0, 5.0]] for fs in ("single", "head_of_household", "married_filing_jointly")}
    return [
        {"name": "rates_under_one_percent", "first_year": 2027,
         "income_tax": {"bracket_vintages": low_rates}},
        {"name": "flat_5", "first_year": 2027, "income_tax": {"brackets": flat}},
        {"name": "zero_tax", "first_year": 2027,
         "income_tax": {"brackets": {fs: [[0.0, 0.0]] for fs in flat}}},
        {"name": "rate_99_above_10m", "first_year": 2027, "income_tax": {"bracket_vintages": [
            {"from": v["from"], "brackets": {fs: rows + [[10_000_000.0, 99.0]]
                                             for fs, rows in v["brackets"].items()}} for v in cl]}},
        {"name": "sd_doubled_2029", "first_year": 2029, "income_tax": {"standard_deduction": {
            "single": 18_000, "head_of_household": 27_000, "married_filing_jointly": 36_000}}},
        {"name": "exemption_2400", "first_year": 2027, "income_tax": {"personal_exemption": 2_400}},
        {"name": "sd_zero_exemption_zero", "first_year": 2028, "income_tax": {
            "standard_deduction": {"single": 0, "head_of_household": 0, "married_filing_jointly": 0},
            "personal_exemption": 0}},
        # Migration phases in from the year the top rate rises (2029), not
        # from first_year; then a second rise in 2030 phases in from 2030.
        {"name": "top_15_from_2029", "first_year": 2027, "income_tax": {"bracket_vintages": [
            cl[0], {"from": 2029, "brackets": _top(cl[1]["brackets"], 15.0)},
            {"from": 2030, "brackets": _top(cl[1]["brackets"], 16.0)}]}},
        # A top rate far outside anything scored so far. (With the migration
        # elasticities before September 27, 2026 it removed every $1M+
        # filer; the clamp at zero is now tested with explicit parameters.)
        {"name": "top_rate_30", "first_year": 2027, "income_tax": {"bracket_vintages": [
            {"from": v["from"], "brackets": _top(v["brackets"], 30.0)} for v in cl]}},
        # A top bracket from $0: the half migration tier starts at current
        # law's top floor, not at $0.
        {"name": "flat_14", "first_year": 2027,
         "income_tax": {"brackets": {fs: [[0.0, 14.0]] for fs in flat}}},
        # Splitting a bracket at the same rate changes no one's tax; the
        # tables must not report floating-point noise as paying more.
        {"name": "split_top_bracket", "first_year": 2027, "income_tax": {"bracket_vintages": [
            {"from": v["from"], "brackets": {fs: rows + [[rows[-1][0] * 1.5, rows[-1][1]]]
                                             for fs, rows in v["brackets"].items()}} for v in cl]}},
        # The capital-gains rate (HRS §235-51(f) alternative tax). Current
        # law's rate scores exactly zero; a rate at or above every bracket
        # rate is the same as taxing gains as ordinary income.
        {"name": "cg_7_25", "first_year": 2027, "income_tax": {"capital_gains_rate": 7.25}},
        {"name": "cg_5", "first_year": 2027, "income_tax": {"capital_gains_rate": 5}},
        {"name": "cg_0", "first_year": 2027, "income_tax": {"capital_gains_rate": 0}},
        {"name": "cg_20", "first_year": 2027, "income_tax": {"capital_gains_rate": 20}},
        {"name": "cg9_from_2029", "first_year": 2029, "income_tax": {"capital_gains_rate": 9.0}},
        # no bracket reaches 9%: the alternative tax never applies
        {"name": "cg9_flat5", "first_year": 2027,
         "income_tax": {"brackets": flat, "capital_gains_rate": 9.0}},
        # low rates up to $500K, then 13%: gains in taxable income below
        # $500K keep the 1.4% rate, and above it pay 9% instead of 13%
        {"name": "cg9_high_floor", "first_year": 2027, "income_tax": {
            "brackets": {fs: [[0.0, 1.4], [500_000.0, 13.0]] for fs in flat},
            "capital_gains_rate": 9.0}},
        {"name": "top14_cg9", "first_year": 2027, "income_tax": {"bracket_vintages": [
            {"from": v["from"], "brackets": _top(v["brackets"], 14.0)} for v in cl],
            "capital_gains_rate": 9.0}},
        {"name": "top14_ordinary", "first_year": 2027, "income_tax": {"bracket_vintages": [
            {"from": v["from"], "brackets": _top(v["brackets"], 14.0)} for v in cl],
            "capital_gains_rate": CG_ORDINARY}},
        # current law's TY2027 standard deduction doubled, gains as ordinary income
        {"name": "sd_double_ordinary", "first_year": 2027, "income_tax": {
            "standard_deduction": {"single": 16_000, "head_of_household": 24_000,
                                   "married_filing_jointly": 32_000},
            "capital_gains_rate": CG_ORDINARY}},
    ]


def _top(brackets: dict, rate: float) -> dict:
    """The schedules with the top bracket's rate set to ``rate``."""
    return {fs: rows[:-1] + [[rows[-1][0], rate]] for fs, rows in brackets.items()}


def invalid_specs() -> list[dict]:
    """Specs IncomeTaxSpec.from_dict must reject (and so must the page)."""
    ok = {"single": [[0, 1.4], [10_000, 5.0]],
          "head_of_household": {"scale": "single", "factor": 1.5},
          "married_filing_jointly": {"scale": "single", "factor": 2}}

    def spec(**it):
        return {"name": "bad", "first_year": 2027, "income_tax": it}

    return [
        {"first_year": 2027},                                            # no name
        {"name": "Bad Name", "first_year": 2027},
        {"name": "bad", "first_year": 2026},
        {"name": "bad", "first_year": 2032},
        {"name": "bad", "first_year": 2027.5},
        {"name": "bad\n", "first_year": 2027},
        {"name": "bad", "first_year": 2027, "income_tax": None},
        {"name": "bad", "first_year": 2027, "baseline": None},
        {"name": "bad", "first_year": 2027, "behavior": None},
        {"name": "bad", "first_year": 2027, "label": None},
        {"name": "bad", "first_year": 2027, "metadata": "notes"},
        {"name": "bad", "first_year": 2027, "model_version": 3},
        spec(brackets=None),
        spec(standard_deduction=None),
        spec(personal_exemption=None),
        spec(bracket_vintages=[{"from": 2027.5, "brackets": ok}]),
        {"name": "bad", "first_year": 2027, "extra": 1},
        {"name": "bad", "first_year": 2027, "baseline": "act46"},
        {"name": "bad", "first_year": 2027, "behavior": []},
        {"name": "bad", "first_year": 2027, "behavior": ["mid", "wild"]},
        spec(brackets={**ok, "single": []}),
        spec(brackets={**ok, "single": [[100, 1.0]]}),                  # must start at 0
        spec(brackets={**ok, "single": [[0, 1.0], [5_000, 2.0], [5_000, 3.0]]}),
        spec(brackets={**ok, "single": [[0, 1.0], [5_000, 2.0], [4_000, 3.0]]}),
        spec(brackets={**ok, "single": [[0, 100.0]]}),
        spec(brackets={**ok, "single": [[0, -1.0]]}),
        spec(brackets={**ok, "single": [[0, "5"]]}),
        spec(brackets={**ok, "single": [[0, 1.0, 2.0]]}),
        spec(brackets={**ok, "single": [[0, 1.0]] + [[1_000 * i, 1.0] for i in range(1, 31)]}),
        spec(brackets={k: v for k, v in ok.items() if k != "head_of_household"}),
        spec(brackets={**ok, "married_filing_separately": [[0, 1.0]]}),
        spec(brackets={**ok, "head_of_household": {"scale": "married_filing_jointly", "factor": 1.5}}),
        spec(brackets={**ok, "head_of_household": {"scale": "single", "factor": 0}}),
        spec(brackets={**ok, "head_of_household": {"scale": "single"}}),
        spec(brackets=ok, bracket_vintages=[{"from": 2027, "brackets": ok}]),
        spec(bracket_vintages=[]),
        spec(bracket_vintages=[{"from": 2028, "brackets": ok}]),         # must start at first_year
        spec(bracket_vintages=[{"from": 2027, "brackets": ok}, {"from": 2027, "brackets": ok}]),
        spec(standard_deduction={"single": 1_000}),
        spec(standard_deduction={"single": -1, "head_of_household": 1, "married_filing_jointly": 1}),
        spec(personal_exemption=-5),
        spec(personal_exemption="1200"),
        spec(personal_exemption=10 ** 400),                            # overflows a float
        spec(tax_rate=5),
        {"name": "bad", "first_year": 2027, "metadata": []},
        {"name": "bad", "first_year": 2027, "metadata": False},
        {"name": "bad", "first_year": 2027, "behavior": [["mid"]]},
        {"name": "bad", "first_year": 2027, "label": "x" * 201},
        spec(brackets={**ok, "constructor": [[0, 1.0]]}),                 # not a status
        spec(brackets={**ok, "toString": [[0, 1.0]]}),
        spec(brackets={**ok, "head_of_household": {"scale": ["single"], "factor": 1.5}}),
        spec(brackets={**ok, "head_of_household": {"scale": "constructor", "factor": 1.5}}),
        spec(brackets={**ok, "single": [[0, 1.0], [2e10, 5.0]]}),         # floor above MAX_FLOOR
        spec(brackets={**ok, "single": [[0, 1.0], [1e308, 5.0]]}),        # x2 overflows
        spec(brackets={**ok, "single": [[0, 1.0], [6e9, 5.0]]}),          # x2 above MAX_FLOOR
        *invalid_cg_specs(),
    ]


def invalid_cg_specs() -> list[dict]:
    """Capital-gains rates IncomeTaxSpec.from_dict must reject; the page
    shows the model's own error text for these (``cg_invalid_messages``)."""
    return [{"name": "bad", "first_year": 2027, "income_tax": {"capital_gains_rate": v}}
            for v in (-1, 100, "9", True, None, "repeal", "Ordinary", {"rate": 9}, [9])]


def valid_edge_specs() -> list[dict]:
    """Unusual but valid specs both parsers must accept."""
    ok = {"single": [[0, 1.4]], "head_of_household": [[0, 1.4]], "married_filing_jointly": [[0, 1.4]]}
    return [
        {"name": "edge-a", "first_year": 2027.0, "metadata": None, "model_version": None},
        {"name": "edge-b", "first_year": 2028, "income_tax": {"brackets": ok, "bracket_vintages": None}},
        {"name": "edge-c", "first_year": 2029, "income_tax": {"bracket_vintages": [{"from": 2029.0, "brackets": ok}]},
         "behavior": ["mid", "mid"], "baseline": "act24", "label": ""},
        {"name": "e", "first_year": 2031, "income_tax": {"standard_deduction": "current_law",
                                                         "personal_exemption": 0.0}},
        # 200 characters counted as code points (400 UTF-16 units)
        {"name": "edge-d", "first_year": 2027, "label": "\U0001F33A" * 200, "metadata": {}},
        {"name": "edge-e", "first_year": 2027, "income_tax": {"brackets": {
            "single": [[0, 1.0], [5e9, 5.0]], "head_of_household": {"scale": "single", "factor": 1.5},
            "married_filing_jointly": {"scale": "single", "factor": 2}}}},   # 1e10 exactly
        *({"name": f"edge-cg-{i}", "first_year": 2027, "income_tax": {"capital_gains_rate": v}}
          for i, v in enumerate((0, 99.99, 9.0, CG_ORDINARY, "current_law"))),
    ]


def _case_from_systems(pop, name, baseline_for, system_for, first_year, calc, dist_years):
    years = pop.years
    res = score_systems(pop, baseline_for, system_for, distribution_years=dist_years, calculator=calc)
    return {
        "name": name, "first_year": first_year,
        "systems": {str(y): {"baseline": system_to_json(baseline_for(y), calc),
                             "reform": system_to_json(system_for(y), calc)} for y in years},
        "expected": {"revenue": res["revenue"],
                     "distribution": {str(y): t for y, t in res["distribution"].items()}},
    }


def _unit_sample(pop: Population, calc: TaxCalculator, cases: list[dict]) -> dict:
    """Per-unit net tax under a few systems (``UNIT_SAMPLE_CASES``), for a
    sample of units; and for ``RESPONSE_SAMPLE``, the same units after the
    behavioral response."""
    rng = np.random.default_rng(7)
    idx = sorted(set(rng.choice(pop.n_units, SAMPLE_UNITS, replace=False).tolist())
                 | set(np.flatnonzero(pop.arrays["agi_2027"] >= 1_000_000).tolist()))
    by_name = {c["name"]: c for c in cases}
    out = []
    for year in (2027, 2031):
        df = frame_for(pop, year, "mid")
        pos = np.argsort(pop.arrays[f"order_{year}"])   # canonical -> frame row
        rows = df.iloc[pos[idx]].reset_index(drop=True)
        for name in UNIT_SAMPLE_CASES:
            case = by_name[name]
            for side in ("baseline", "reform"):
                cfg = (case["baseline_for"] if side == "baseline" else case["system_for"])(year)
                u = calc.unit_liabilities(rows, cfg)
                out.append({"case": case["name"], "year": year, "side": side, "units": idx,
                            "before_credits": u["before_credits"].tolist(),
                            "credits": u["credits"].tolist(), "net": u["net"].tolist()})
    name, scenario, year = RESPONSE_SAMPLE
    case = by_name[name]
    path = top_rate_path(case["baseline_for"], case["system_for"], pop.years, calc)
    cfg = case["system_for"](year)
    # the responded frame score_with_response scores (same row order as frame_for)
    _, adj, _ = score_with_response(
        frame_for(pop, year, scenario), SCENARIOS[scenario].behavioral_params, target_year=year,
        baseline_cfg=case["baseline_for"](year), scenario_cfg=cfg, calculator=calc,
        top_rate_path=path)
    rows = adj.iloc[np.argsort(pop.arrays[f"order_{year}"])[idx]].reset_index(drop=True)
    u = calc.unit_liabilities(rows, cfg)
    response = {"case": name, "scenario": scenario, "year": year, "units": idx,
                "agi": rows["income"].tolist(), "cg_share": rows["synthetic_cg_share"].tolist(),
                "weight": rows["weight"].tolist(), "net": u["net"].tolist()}
    return {"units": out, "response": response}


def _households(calc: TaxCalculator) -> list[dict]:
    """Single-return calculations for the page's household calculator."""
    rng = random.Random(11)
    statuses = ["single", "married_filing_jointly", "head_of_household", "married_filing_separately"]
    systems: dict[str, TaxSystemConfig] = {
        "current_2027": _statute(TaxSystemRegistry.get_sb3125_cd2_system)(2027),
        "current_2031": _statute(TaxSystemRegistry.get_sb3125_cd2_system)(2031),
        "act46_2029": _statute(TaxSystemRegistry.get_act46_system)(2029),
        "cg9_2027": dataclasses.replace(current_law_system(2027), capital_gains_rate_pct=9.0),
        "ordinary_2027": dataclasses.replace(current_law_system(2027), capital_gains_rate_pct=None)}
    out = []
    for i in range(160):
        fs = statuses[i % 4]
        agi = float(rng.choice([0, 8_000, 14_999, 15_000, 29_999, 45_000, 60_000, 99_999, 100_000,
                                175_000, 250_000, 400_000, 750_000, 1_200_000, 5_000_000])
                    + rng.choice([0, 0, 0.5, 137.25]))
        deps = rng.choice([0, 0, 1, 2, 3, 5])
        item = float(rng.choice([0, 0, 5_000, 20_000, 60_000, 250_000]))
        cg = rng.choice([0.0, 0.0, 0.1, 0.5, 0.9])
        frame = pd.DataFrame({"income": [agi], "filing_status": [fs], "num_dependents": [deps],
                              "hi_itemized_deduction": [item], "synthetic_cg_share": [cg],
                              "weight": [1.0]})
        for key, cfg in systems.items():
            u = calc.unit_liabilities(frame, cfg)
            out.append({"system": key, "filing_status": fs, "agi": agi, "dependents": deps,
                        "itemized": item, "cg_share": cg,
                        "before_credits": float(u["before_credits"][0]),
                        "credits": float(u["credits"][0]), "net": float(u["net"][0])})
    return out, {k: system_to_json(v, calc) for k, v in systems.items()}


def write_golden(pop: Population, path, *, n_random: int = 12, n_random_cg: int = 4) -> str:
    calc = TaxCalculator()
    spec_dicts = presets(calc) + edge_specs() + random_specs(n_random) + random_cg_specs(n_random_cg)
    specs = [IncomeTaxSpec.from_dict(d) for d in spec_dicts]
    preset_names = {d["name"] for d in presets(calc)}
    raw_cases = [{"name": "act24_vs_act46", "first_year": 2027,
                  "baseline_for": _statute(TaxSystemRegistry.get_act46_system),
                  "system_for": _statute(TaxSystemRegistry.get_sb3125_cd2_system)}]
    raw_cases += [{"name": s.name, "first_year": s.first_year, "baseline_for": s.baseline_for,
                   "system_for": s.system_for} for s in specs]
    cases = []
    for c in raw_cases:
        # Full distribution for the anchor case and presets; one year for the rest.
        dist = pop.years if c["name"] == "act24_vs_act46" or c["name"] in preset_names else (2029,)
        cases.append(_case_from_systems(pop, c["name"], c["baseline_for"], c["system_for"],
                                        c["first_year"], calc, dist))
    households, household_systems = _households(calc)
    spec_resolution = [{"spec": d, "systems": {str(y): system_to_json(s.system_for(y), calc)
                                               for y in pop.years}}
                       for d, s in zip(spec_dicts, specs)]
    invalid = invalid_specs()
    for d in invalid:
        try:
            IncomeTaxSpec.from_dict(d)
        except ValueError:
            continue
        raise AssertionError(f"IncomeTaxSpec accepted an invalid spec: {d}")
    cg_messages = []
    for d in invalid_cg_specs():
        try:
            IncomeTaxSpec.from_dict(d)
        except ConfigError as e:
            cg_messages.append({"spec": d, "message": str(e)})
    for d in valid_edge_specs():
        IncomeTaxSpec.from_dict(d)
    out = {"population_meta": {k: pop.meta[k] for k in ("years", "n_units", "n_households")},
           "invalid_specs": invalid,
           "cg_invalid_messages": cg_messages,
           "valid_edge_specs": valid_edge_specs(),
           "cases": cases, "unit_sample": _unit_sample(pop, calc, raw_cases),
           "households": households, "household_systems": household_systems,
           "spec_resolution": spec_resolution}
    import gzip
    Path(path).write_bytes(gzip.compress(json.dumps(out).encode(), compresslevel=9, mtime=0))
    return f"{len(cases)} cases, {len(households)} households -> {Path(path).name}"
