"""Score tax systems on the simulator population with the real pipeline.

This is the reference the browser kernel (``site/assets/simulator/kernel.js``)
must reproduce: it calls ``score_with_response`` and
``generate_quintile_report`` on frames rebuilt from the population, the same
functions the Act 24 estimates use. Scoring the registry's Act 24 against
Act 46 here on the model's own gains base (``gains="model"``) reproduces the
published Act 24 bracket figures and distribution tables; specs are scored on
the DOTAX-anchored base with the statutory capital-gains alternative tax.
"""
from __future__ import annotations

from typing import Callable, Iterable

import numpy as np
import pandas as pd

from tax_modeler.config.tax_system_config import TaxCalculator, TaxSystemConfig
from tax_modeler.scenarios.behavioral_response import score_with_response, top_rate_path
from tax_modeler.scenarios.quintile_analysis import generate_quintile_report

from .population import Population, frame_for
from .scenarios import SCENARIOS

SystemFactory = Callable[[int], TaxSystemConfig]

# Distribution columns reported: generate_quintile_report's, with its
# Act 46 / CD1 names changed to baseline / reform (here the baseline is
# usually current law, Act 24).
REPORT_NAMES = {"avg_per_hh_baseline_tax": "avg_per_hh_act46_tax",
                "avg_per_hh_reform_tax": "avg_per_hh_cd1_tax",
                "total_baseline_$M": "total_act46_$M", "total_reform_$M": "total_cd1_$M",
                "avg_baseline_tax": "avg_act46_tax", "avg_reform_tax": "avg_cd1_tax"}
QUINTILE_COLUMNS = ("household_count", "avg_per_hh_baseline_tax", "avg_per_hh_reform_tax",
                    "avg_per_hh_bracket_change", "total_baseline_$M", "total_reform_$M",
                    "total_bracket_$M", "pct_pay_more", "pct_pay_less", "pct_no_change")
CLASS_COLUMNS = ("filer_count", "avg_baseline_tax", "avg_reform_tax", "avg_bracket_change",
                 "total_baseline_$M", "total_reform_$M", "total_bracket_$M",
                 "pct_pay_more", "pct_pay_less", "pct_no_change")
# A tax change within half a cent of zero is no change (floating-point noise
# from, e.g., splitting a bracket without changing any rate).
NO_CHANGE_TOLERANCE = 0.005


def score_systems(
    pop: Population,
    baseline_for: SystemFactory,
    system_for: SystemFactory,
    *,
    years: Iterable[int] | None = None,
    scenarios: Iterable[str] = ("low", "mid", "high"),
    distribution_years: Iterable[int] = (),
    calculator: TaxCalculator | None = None,
    gains: str = "anchored",
) -> dict:
    """Revenue for every (scenario, year), static and after the behavioral
    response, plus MID distribution tables for ``distribution_years``.

    ``behavioral_$M`` is ``score_with_response``'s: the plan scored on the
    population after it responds, less current law on the population before
    anyone does, so a filer who moves away costs their whole tax. Migration
    phases each change in the top rate in from the year it takes effect
    (``top_rate_path``). Years before a plan first raises the top rate add
    nothing; after a temporary rise, filers who left and have not yet
    returned (the fall phases in too) still cost their tax, even in years
    when the plan equals current law. Money in $M; the distribution tables
    use ``generate_quintile_report``'s columns with ``act46``/``cd1``
    renamed ``baseline``/``reform`` (``REPORT_NAMES``).

    ``gains``: the capital-gains base (``population.unit_arrays``): the
    simulator's DOTAX-anchored base, or ``"model"``, the Act 24 page's."""
    calc = calculator or TaxCalculator()
    years = tuple(years or pop.years)
    path = top_rate_path(baseline_for, system_for, pop.years, calc)
    revenue = []
    for s in scenarios:
        params = SCENARIOS[s].behavioral_params
        for y in years:
            rev, _, diag = score_with_response(
                frame_for(pop, y, s, gains), params, target_year=y, baseline_cfg=baseline_for(y),
                scenario_cfg=system_for(y), calculator=calc, top_rate_path=path)
            revenue.append({
                "scenario": s, "tax_year": y,
                **{k: rev[k] for k in ("baseline_$M", "reform_$M", "static_$M", "behavioral_$M")},
                "filers_1m_post_response": diag["filers_1m_post_response"],
            })
    distribution = {}
    for y in distribution_years:
        q, b, _ = generate_quintile_report(
            frame_for(pop, y, "mid", gains), baseline_for(y), system_for(y), {}, calc,
            scenario_params=None, quintile_breaks=np.asarray(pop.meta["quintile_breaks"]),
            no_change_tolerance=NO_CHANGE_TOLERANCE)
        col = lambda r, c: float(r[REPORT_NAMES.get(c, c)])  # noqa: E731
        distribution[y] = {
            "quintile": [{"group": str(r["quintile"]), **{c: col(r, c) for c in QUINTILE_COLUMNS}}
                         for _, r in q.iterrows()],
            "income_class": [{"group": str(r["income_bracket"]), **{c: col(r, c) for c in CLASS_COLUMNS}}
                             for _, r in b.iterrows()],
        }
    return {"revenue": revenue, "distribution": distribution}


def score_spec(pop: Population, spec, **kwargs) -> dict:
    """Score an ``IncomeTaxSpec`` against current law (Act 24)."""
    kwargs.setdefault("scenarios", [s for s in ("low", "mid", "high")
                                    if s in spec.behavior or "static" in spec.behavior])
    return score_systems(pop, spec.baseline_for, spec.system_for, **kwargs)


def revenue_frame(result: dict) -> pd.DataFrame:
    return pd.DataFrame(result["revenue"])
