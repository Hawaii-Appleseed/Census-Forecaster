"""Preset specs for the simulator page, built from the model's bracket CSV.

Each is an ``IncomeTaxSpec`` dict scored against current law (Act 24):

* ``current_law`` — no change; scores zero.
* ``act46`` — Act 46's own two bracket vintages (TY2027-28 and TY2029+), i.e.
  undoing Act 24's rate changes. Its static change is the Act 24 page's static
  bracket figure with the sign reversed, in every year. (Not its
  after-response figure: against Act 24 this is a rate cut, and the model
  gives rate cuts no behavioral response.)
* ``sb3125_sd1`` / ``hb2306_hd1`` — those bills' bracket schedules only (SD1's
  dependent-care credit changes are not modeled here).
* ``top_rate_14`` — current law with the 13% top rate raised to 14%, in both
  vintages.
"""
from __future__ import annotations

from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemConfig, TaxSystemRegistry
from tax_modeler.reform.income_tax_spec import SPEC_STATUSES, current_law_system

FIRST_YEAR = 2027
VINTAGE_YEARS = (2027, 2029)   # Act 24's (and Act 46's) bracket vintages


def _schedules(calc: TaxCalculator, config: TaxSystemConfig) -> dict:
    out = {}
    for spec_fs, csv_fs in SPEC_STATUSES.items():
        b = calc.brackets_for(config, csv_fs)
        out[spec_fs] = [[float(f), float(r)] for f, r in zip(b["income_min"], b["rate"])]
    return out


def current_law_vintages(calc: TaxCalculator | None = None) -> list[dict]:
    calc = calc or TaxCalculator()
    return [{"from": y, "brackets": _schedules(calc, current_law_system(y))} for y in VINTAGE_YEARS]


def presets(calc: TaxCalculator | None = None) -> list[dict]:
    calc = calc or TaxCalculator()
    act46 = [{"from": y, "brackets": _schedules(calc, TaxSystemRegistry.get_act46_system(y))}
             for y in VINTAGE_YEARS]
    top14 = current_law_vintages(calc)
    for v in top14:
        for rows in v["brackets"].values():
            rows[-1][1] = 14.0
    return [
        {"name": "current_law", "label": "Current law (Act 24)", "first_year": FIRST_YEAR,
         "income_tax": {}},
        {"name": "act46", "label": "Act 46's brackets (undo Act 24's rate changes)",
         "first_year": FIRST_YEAR, "income_tax": {"bracket_vintages": act46}},
        {"name": "sb3125_sd1", "label": "SB 3125 SD1 brackets", "first_year": FIRST_YEAR,
         "income_tax": {"brackets": _schedules(calc, TaxSystemRegistry.get_sb3125_sd1_system(2027))}},
        {"name": "hb2306_hd1", "label": "HB 2306 HD1 brackets", "first_year": FIRST_YEAR,
         "income_tax": {"brackets": _schedules(calc, TaxSystemRegistry.get_hb2306_hd1_system(2027))}},
        {"name": "top_rate_14", "label": "Top rate 14% (was 13%)", "first_year": FIRST_YEAR,
         "income_tax": {"bracket_vintages": top14}},
    ]
