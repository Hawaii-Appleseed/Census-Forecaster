"""Tax systems as plain data, for the browser kernel and its fixtures.

A system is what the kernel needs to tax one unit: per filing status the
bracket schedule ``[[floor, rate %], ...]`` and standard deduction, the
personal exemption, the tax year whose credit law applies
(``HawaiiTaxCredits(year=...)``: the food/excise table changes after TY2027),
and ``capital_gains_rate``: the HRS §235-51(f) alternative tax rate in
percent, or ``"ordinary"`` for none (``tax_modeler.liability.cg_alternative``).
"""
from __future__ import annotations

from tax_modeler.config.tax_system_config import CSV_STATUSES, TaxCalculator, TaxSystemConfig
from tax_modeler.reform.income_tax_spec import CG_ORDINARY


def system_to_json(config: TaxSystemConfig, calc: TaxCalculator | None = None) -> dict:
    calc = calc or TaxCalculator()
    if config.credit_scenario is not None:
        # The kernel implements current-law credits only.
        raise ValueError(f"{config.name}: credit_scenario {config.credit_scenario!r} "
                         "is not modeled by the simulator")
    if config.surcharges:
        raise ValueError(f"{config.name}: surcharges are not modeled by the simulator")
    if config.cg_alt_tax != "statute":
        # The kernel implements the statutory alternative tax only; the
        # stacked shortcut is kept for the registry systems' published runs.
        raise ValueError(f"{config.name}: cg_alt_tax {config.cg_alt_tax!r} is not modeled by "
                         "the simulator (use dataclasses.replace(config, cg_alt_tax='statute'))")
    rate = config.capital_gains_rate_pct
    out = {"name": config.name, "credit_year": int(config.year),
           "personal_exemption": float(config.personal_exemption),
           "capital_gains_rate": CG_ORDINARY if rate is None else float(rate),
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
