"""Tax systems as plain data, for the browser kernel and its fixtures.

A system is what the kernel needs to tax one unit: per filing status the
bracket schedule ``[[floor, rate %], ...]`` and standard deduction, the
personal exemption, and the tax year whose credit law applies
(``HawaiiTaxCredits(year=...)``: the food/excise table changes after TY2027).
"""
from __future__ import annotations

from tax_modeler.config.tax_system_config import CSV_STATUSES, TaxCalculator, TaxSystemConfig


def system_to_json(config: TaxSystemConfig, calc: TaxCalculator | None = None) -> dict:
    calc = calc or TaxCalculator()
    if config.credit_scenario is not None:
        # The kernel implements current-law credits only.
        raise ValueError(f"{config.name}: credit_scenario {config.credit_scenario!r} "
                         "is not modeled by the simulator")
    if config.surcharges:
        raise ValueError(f"{config.name}: surcharges are not modeled by the simulator")
    out = {"name": config.name, "credit_year": int(config.year),
           "personal_exemption": float(config.personal_exemption),
           "brackets": {}, "standard_deduction": {}}
    for fs in CSV_STATUSES:
        b = calc.brackets_for(config, fs)
        out["brackets"][fs] = [[float(f), float(r)] for f, r in zip(b["income_min"], b["rate"])]
        out["standard_deduction"][fs] = float(calc.standard_deduction_for(config, fs))
    return out


def food_excise_tables(years) -> dict:
    """Statutory food/excise credit schedules by tax year
    (``credits.hi_food_excise``): single and joint tables, amount factor."""
    from tax_modeler.credits.hi_food_excise import hawaii_food_excise_parameters

    out = {}
    for y in years:
        p = hawaii_food_excise_parameters(y)
        out[str(y)] = {"single": [list(r) for r in p.single], "joint": [list(r) for r in p.joint],
                       "amount_pct": p.amount_pct, "income_threshold_factor": p.income_threshold_factor}
    return out
