"""Tax systems as plain data, for the browser kernel and its fixtures.

A system is what the kernel needs to tax one unit: per filing status the
bracket schedule ``[[floor, rate %], ...]`` and standard deduction, the
personal exemption, the tax year whose credit law applies
(``HawaiiTaxCredits(year=...)``: the food/excise table and the care credit's limits change after TY2027),
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
        # stacked shortcut is only the registry systems' default.
        raise ValueError(f"{config.name}: cg_alt_tax {config.cg_alt_tax!r} is not modeled by "
                         "the simulator (use dataclasses.replace(config, cg_alt_tax='statute'))")
    rate = config.capital_gains_rate_pct
    out = {"name": config.name, "credit_year": int(config.year),
           "personal_exemption": float(config.personal_exemption),
           "capital_gains_rate": CG_ORDINARY if rate is None else float(rate),
           "brackets": {}, "standard_deduction": {}}
    for fs in CSV_STATUSES:
        b = calc.brackets_for(config, fs)
        out["brackets"][fs] = [[float(f), float(r)] for f, r in zip(b["income_min"], b["rate"], strict=False)]
        out["standard_deduction"][fs] = float(calc.standard_deduction_for(config, fs))
    return out


def food_excise_tables(years) -> dict:
    """Statutory credit schedules by tax year, for the kernel: the food/excise credit
    (``credits.hi_food_excise``: single and joint tables, amount factor) and the care credit
    (``credits.hi_cdcc``, key ``cdcc``): the expense limits in force that year, the
    applicable-percentage schedule, and the CPS ASEC donor spending (``donors``, keyed by one child or
    two or more: the amounts and their weights, in the order the expected expense sums them), which a
    household calculator turns into the expected expenses the credit counts. The population carries
    each unit's expected expense already (``care_exp_<year>``)."""
    from tax_modeler.credits import hi_cdcc
    from tax_modeler.credits.hi_food_excise import hawaii_food_excise_parameters

    donors = {str(b): {"x": [float(v) for v in x], "p": [float(v) for v in p]}
              for b, (x, p) in hi_cdcc.default_donors().items()}
    out = {}
    for y in years:
        p = hawaii_food_excise_parameters(y)
        out[str(y)] = {"single": [list(r) for r in p.single], "joint": [list(r) for r in p.joint],
                       "amount_pct": p.amount_pct, "income_threshold_factor": p.income_threshold_factor,
                       "cdcc": {"caps": [float(c) for c in hi_cdcc.caps_for_year(y)],
                                "percent": [[float(c), float(r)] for c, r in hi_cdcc.PERCENT_SCHEDULE],
                                "percent_above": float(hi_cdcc.PERCENT_ABOVE),
                                "donors": donors}}
    return out
