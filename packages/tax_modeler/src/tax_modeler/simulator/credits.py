"""The simulator's working-family credits: the state EITC and the refundable
food/excise tax credit (a spec's ``credits`` block).

Act 163 (SLH 2023) raised both for TY2023-2027: the EITC (HRS §235-55.75)
from 20% to 40% of the federal credit, and the food/excise credit
(HRS §235-55.85) to a larger table. It is repealed on December 31, 2027, so
current law is Act 163's credits through TY2027 and the earlier ones after.

The simulator does not score these on its own population, which has no
federal EITC and no take-up. It prices them with the working-family credits
run (``chain/forecast_working_family_credits.py``, the "Expiring Relief" page):
each credit under both laws, with take-up calibrated to DOTAX claims. The
EITC is a share of the federal credit with take-up held fixed, so its cost is
linear in the share: (share in points) x (the 20% credit / 20). The
food/excise credit is one of the two tables. Credits are refundable and do
not depend on the brackets, so the change is the same in every behavioral
scenario and is added to the income tax change, not scored with it.

The browser kernel (``site/assets/simulator/kernel.js``, ``creditChanges``)
does the same arithmetic in the same order; the golden fixtures check it.

The table also carries the federal EITC's parameters for each year
(``tax_modeler.credits.eitc``, CPI-extrapolated past the last Rev. Proc., as
the working-family credits run uses them), so the page's household
calculator can show one household's state EITC: the state's share of its
federal credit, with no take-up.
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

#: The last tax year of Act 163's expansions.
ACT163_LAST_YEAR = 2027
#: The EITC's share of the federal credit (%), with Act 163 and without it.
EITC_PERCENT = {"act163": 40.0, "pre_act163": 20.0}


def current_law_food_excise(year: int) -> str:
    return "act163" if year <= ACT163_LAST_YEAR else "pre_act163"


def current_law_eitc_percent(year: int) -> float:
    return EITC_PERCENT[current_law_food_excise(year)]


def load_credit_table(run_dir, years) -> dict:
    """The credits' cost by tax year from a working-family credits run
    directory (``revenue_by_year.csv`` and ``manifest.json``), for ``years``."""
    d = Path(run_dir)
    df = pd.read_csv(d / "revenue_by_year.csv").set_index("tax_year")
    manifest = json.loads((d / "manifest.json").read_text())
    rates = manifest["params"]["eitc_rates"]
    if sorted(rates) != [0.2, 0.4]:
        raise ValueError(f"{d}: expected EITC rates of 20% and 40% of federal, got {rates}")
    missing = sorted(set(years) - set(df.index))
    if missing:
        raise ValueError(f"{d}/revenue_by_year.csv has no tax years {missing}")
    per_point, food = {}, {"act163": {}, "pre_act163": {}}
    for y in years:
        r = df.loc[y]
        # take-up is the same under both rates, so 40% is exactly twice 20%
        if abs(r["eitc_expanded_$M"] - 2 * r["eitc_reverted_$M"]) > 1e-9 * r["eitc_expanded_$M"]:
            raise ValueError(f"{d}: TY{y} EITC at 40% is not twice the 20% credit")
        per_point[str(y)] = float(r["eitc_reverted_$M"]) / EITC_PERCENT["pre_act163"]
        food["act163"][str(y)] = float(r["food_expanded_$M"])
        food["pre_act163"][str(y)] = float(r["food_reverted_$M"])
    p = manifest["params"]
    return {
        "federal_eitc": {str(y): federal_eitc_json(y) for y in years},
        "years": [int(y) for y in years],
        "current_law": {"eitc_percent": {str(y): current_law_eitc_percent(y) for y in years},
                        "food_excise": {str(y): current_law_food_excise(y) for y in years}},
        # $M of credit per percentage point of the federal EITC
        "eitc_per_point_$M": per_point,
        # $M of food/excise credit under each table
        "food_excise_$M": food,
        "source": {"run": "working_family_credits", "created_at": manifest.get("created_at"),
                   "git_sha": manifest.get("git_sha"),
                   "params_fingerprint": manifest.get("params_fingerprint"),
                   "takeup_eitc": p.get("takeup_eitc"), "takeup_food": p.get("takeup_food")},
    }


def federal_eitc_json(year: int) -> dict:
    """The federal EITC's parameters for ``year``, by number of qualifying
    children (0-3, 3 meaning three or more)."""
    from tax_modeler.credits.eitc import eitc_parameters_for_year

    p = eitc_parameters_for_year(year, extrapolate=True)
    return {"investment_income_limit": p.investment_income_limit,
            "by_children": [{"max_credit": c.max_credit, "phase_in_rate": c.phase_in_rate,
                             "phaseout_start_single": c.phaseout_start_single,
                             "phaseout_start_joint": c.phaseout_start_joint,
                             "phaseout_rate": c.phaseout_rate}
                            for c in (p.by_children[n] for n in range(4))]}


def credit_changes(eitc_percent: float | None, food_excise: str | None, first_year: int,
                   table: dict) -> list[dict]:
    """Change in revenue ($M; negative = the state pays out more) by tax year
    from a spec's credits, against current law. None keeps current law."""
    cl = table["current_law"]
    per_point, food = table["eitc_per_point_$M"], table["food_excise_$M"]
    out = []
    for y in table["years"]:
        k = str(y)
        eitc = fe = 0.0
        if y >= first_year:
            if eitc_percent is not None:
                eitc = -(eitc_percent - cl["eitc_percent"][k]) * per_point[k]
            if food_excise is not None:
                fe = -(food[food_excise][k] - food[cl["food_excise"][k]][k])
        out.append({"tax_year": y, "eitc_$M": eitc, "food_excise_$M": fe, "total_$M": eitc + fe})
    return out


def spec_credit_changes(spec, table: dict) -> list[dict]:
    """:func:`credit_changes` for an ``IncomeTaxSpec``."""
    return credit_changes(spec.eitc_percent, spec.food_excise, spec.first_year, table)
