"""What the translator must produce for each fixture bill, in one place.

Shared by ``tests/fiscal_notes`` (recorded responses) and ``python -m fiscal_notes eval``
(the real model). A bill's brackets are compared, tax year by tax year, with the
registry's own schedule for that bill (the way the spike did).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from fiscal_notes.translator import CheckResult, effective_brackets

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fiscal_notes"
BILLS_DIR = FIXTURES / "bills"
RESPONSES_DIR = FIXTURES / "responses"

YEARS = range(2027, 2032)

#: SB 3125 SD1's own 2029 single-filer schedule (the registry carries the 2027 one forward).
SB3125_SD1_2029_SINGLE = [[0, 1.4], [19200, 3.2], [24000, 5.5], [36000, 6.4], [48000, 6.8],
                          [125000, 7.2], [175000, 8.25], [225000, 9.0], [275000, 10.0],
                          [325000, 11.0]]


@dataclass(frozen=True)
class Expectation:
    label: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True)
class Case:
    verdict: str
    #: tax year -> {"single"|...: schedule}; a year left out is not compared
    schedules: Callable[[int], dict | None] = lambda year: None
    schedule_years: tuple[int, ...] = ()
    #: extra checks on the spec: spec -> [(label, ok)]
    extra: Callable[[dict | None], list[tuple[str, bool]]] = lambda spec: []


def registry_schedule(fn: Callable, year: int) -> dict[str, list[list[float]]]:
    from tax_modeler.config.tax_system_config import TaxCalculator
    from tax_modeler.reform.income_tax_spec import SPEC_STATUSES

    calc = TaxCalculator()
    cfg = fn(year)
    out = {}
    for spec_fs, csv_fs in SPEC_STATUSES.items():
        b = calc.brackets_for(cfg, csv_fs)
        out[spec_fs] = [[float(f), float(r)] for f, r in zip(b["income_min"], b["rate"], strict=False)]
    return out


def _registry(name: str) -> Callable[[int], dict]:
    def schedules(year: int) -> dict:
        from tax_modeler.config.tax_system_config import TaxSystemRegistry
        from tax_modeler.reform.income_tax_spec import current_law_system

        fn = {
            "hb2306_hd1": TaxSystemRegistry.get_hb2306_hd1_system,
            "sb3125_sd1": TaxSystemRegistry.get_sb3125_sd1_system,
            "current_law": current_law_system,
            "act46": TaxSystemRegistry.get_act46_system,
        }[name]
        return registry_schedule(fn, year)
    return schedules


def _sd1_schedules(year: int) -> dict:
    if year <= 2028:
        return _registry("sb3125_sd1")(year)
    return {"single": [[float(f), float(r)] for f, r in SB3125_SD1_2029_SINGLE]}


def _hd1_extra(spec):
    credits = (spec or {}).get("credits")
    return [("credits = eitc_percent 40, food_excise act163",
             credits == {"eitc_percent": 40, "food_excise": "act163"})]


def _act46_extra(spec):
    sd = ((spec or {}).get("income_tax") or {}).get("standard_deduction")
    return [("stepped standard deduction not in the spec", sd is None)]


def _null_extra(spec):
    return [("spec is null", spec is None)]


# HB 2306 as introduced, pinned from the translator's output after reading the bill: sections 1 and 2
# repeal the bracketed (2027+/2029+ and 2028+) standard-deduction steps and bracket schedules, which
# leaves the TY2025 bracket schedules (checked line by line against the bill's unbracketed tables) and the
# TY2026 standard deduction ($8,000 / $12,000 / $16,000) in force through 2031; section 4 extends Act 163
# to 2032. Section 3 (dependent care credit) cannot be modeled.
INTRODUCED_SPEC = {'first_year': 2027,
 'income_tax': {'bracket_vintages': [{'from': 2027,
                                      'brackets': {'single': [[0, 1.4],
                                                              [9600, 3.2],
                                                              [14400, 5.5],
                                                              [19200, 6.4],
                                                              [24000, 6.8],
                                                              [36000, 7.2],
                                                              [48000, 7.6],
                                                              [125000, 7.9],
                                                              [175000, 8.25],
                                                              [225000, 9.0],
                                                              [275000, 10.0],
                                                              [325000, 11.0]],
                                                   'head_of_household': [[0, 1.4],
                                                                         [14400, 3.2],
                                                                         [21600, 5.5],
                                                                         [28800, 6.4],
                                                                         [36000, 6.8],
                                                                         [54000, 7.2],
                                                                         [72000, 7.6],
                                                                         [187500, 7.9],
                                                                         [262500, 8.25],
                                                                         [337500, 9.0],
                                                                         [412500, 10.0],
                                                                         [487500, 11.0]],
                                                   'married_filing_jointly': [[0, 1.4],
                                                                              [19200, 3.2],
                                                                              [28800, 5.5],
                                                                              [38400, 6.4],
                                                                              [48000, 6.8],
                                                                              [72000, 7.2],
                                                                              [96000, 7.6],
                                                                              [250000, 7.9],
                                                                              [350000, 8.25],
                                                                              [450000, 9.0],
                                                                              [550000, 10.0],
                                                                              [650000,
                                                                               11.0]]}}],
                'standard_deduction': {'single': 8000,
                                       'head_of_household': 12000,
                                       'married_filing_jointly': 16000}},
 'credits': {'eitc_percent': 40, 'food_excise': 'act163'}}


def _introduced_extra(spec):
    return [("spec equals the pinned reading of the bill", spec == INTRODUCED_SPEC)]


CASES: dict[str, Case] = {
    "2026_HB2306_HD1": Case("partial", _registry("hb2306_hd1"), tuple(YEARS), _hd1_extra),
    "2026_SB3125_SD1": Case("partial", _sd1_schedules, (2027, 2028, 2029, 2030, 2031)),
    "2026_SB3125_CD2": Case("partial", _registry("current_law"), tuple(YEARS)),
    "2024_HB2404_CD1": Case("partial", _registry("act46"), tuple(YEARS), _act46_extra),
    "2024_HB2404_SD1": Case("out_of_scope", extra=_null_extra),
    "2026_HB2306_Introduced": Case("partial", extra=_introduced_extra),
}


def bill_text(case: str) -> str:
    return (BILLS_DIR / f"{case}.txt").read_text()


def response_text(case: str) -> str:
    return (RESPONSES_DIR / f"{case}.json").read_text()


@dataclass
class Evaluation:
    case: str
    expectations: list[Expectation] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(e.ok for e in self.expectations)


def evaluate(case_name: str, result: CheckResult) -> Evaluation:
    """Every expectation for ``case_name`` against a checked result."""
    case = CASES[case_name]
    ev = Evaluation(case_name)
    ev.expectations.append(Expectation("no problems", not result.problems,
                                       "; ".join(result.problems)))
    ev.expectations.append(Expectation(f"verdict {case.verdict}", result.verdict == case.verdict,
                                       f"got {result.verdict}"))
    for year in case.schedule_years:
        want = case.schedules(year)
        got = effective_brackets(result.spec, year) if result.spec else None
        if got is None:
            ev.expectations.append(Expectation(f"brackets {year}", False, "spec has no schedule"))
            continue
        bad = [fs for fs in want
               if [[float(a), float(b)] for a, b in got.get(fs, [])] != want[fs]]
        ev.expectations.append(Expectation(f"brackets {year}", not bad,
                                           f"differs for {', '.join(bad)}" if bad else ""))
    for label, ok in case.extra(result.spec):
        ev.expectations.append(Expectation(label, ok))
    return ev
