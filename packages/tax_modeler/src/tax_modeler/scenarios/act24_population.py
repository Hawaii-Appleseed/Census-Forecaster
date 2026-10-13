"""The population the Act 24 estimates are scored on.

``forecast_sb3125_enhanced.py`` (the Act 24 page) and the tax simulator's
scoring population (``tax_modeler.simulator.population``) both build their
tax units here, so the simulator scores exactly the population the published
estimates use; ``forecast_act24_vs_pre_act46.py`` and
``scripts/dotax_scorecard.py`` build theirs here too.

Two steps:

* :func:`build_units` — the calibrated base (its capital gains already
  anchored to DOTAX Table 21 at the base year, before the rake:
  ``pipeline.calibrate_anchored``) plus the synthetic $1M+ tail (Pareto
  ``alpha``) with its gains anchored to the $1M+ Table 21 target, and the tail
  calibrated to the DOTAX $1M+ tax target (iterated until re-scoring lands on
  it, on the anchored tail) and then aged to the PUMS income dollar year by the
  scenario's aging spec, re-scored on the calibration's deduction basis.
* :func:`project_units` — one tax year through the one aging engine
  (``projection.engine.project_units``): CBO component aging (or the old
  county-median scalar, ``AgingSpec(engine="scalar")``), the scenario's
  top-share drift, an optional recession shock, then the tax of every unit
  whose income those steps changed is recomputed.

Capital gains are scored on the DOTAX-anchored base with the statutory
alternative tax (HRS §235-51(f)), as on the tax simulator and the
capital-gains page: :func:`mid_gains_factors` gives the MID population's
scale factor per DOTAX class and year (DOTAX anchors MID), every scenario's
projected frame is rescaled with them (``cg_anchor.apply_anchor_factors``),
and :func:`statute` scores a registry law with the statutory tax instead of
its stacked shortcut.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Optional

import pandas as pd

from tax_modeler.projection.engine import AgingSpec


def build_units(
    base_calibrated: pd.DataFrame,
    *,
    alpha: float,
    spec: AgingSpec,
    ded_params,
    cal_tax_year: int,
    enrich: bool = False,
) -> tuple[pd.DataFrame, float]:
    """Synthesize the $1M+ tail on the calibrated base; return (units, tail_k).

    The base carries its gains anchored at the base year (``load_calibrated_base``
    refuses one that does not), so its shares are not imputed here; the tail's
    gains are anchored to the $1M+ Table 21 target (``cg_anchor.anchor_tail_gains``)
    before ``tail_k`` is fit, and ``tail_k`` scales only the tail's ordinary
    income, so its gains stay at the anchor. The tail's ordinary income is scaled until its re-scored
    tax is DOTAX's $1M+
    tax target (``calibrate_synthetic_tail_to_tax_target``; ``tail_k`` is that
    scale), then aged from the target's tax year to the PUMS income dollar
    year (``age_synthetic_tail``) by ``spec``'s engine and drift, since
    :func:`project_units` grows every unit from there with the same ``spec``.

    ``ded_params`` / ``cal_tax_year`` are the deduction params and vintage the
    base was calibrated under; re-scoring on any other basis derives a wrong
    ``tail_k``. ``enrich`` re-runs ``enrich_for_credits`` after synthesis, as
    the distributional pass does: it fills the synthetic units'
    ``total_cash_income`` (used to rank households) and leaves every tax input
    unchanged.
    """
    from tax_modeler.calibration.cg_anchor import anchor_tail_gains
    from tax_modeler.pipeline import compute_base_tax, enrich_for_credits
    from tax_modeler.scenarios.top_income_synthesis import (
        age_synthetic_tail,
        calibrate_synthetic_tail_to_tax_target,
        synthesize_top_filers,
    )

    def score(u: pd.DataFrame) -> pd.DataFrame:
        return compute_base_tax(u, deduction_params=ded_params, tax_year=cal_tax_year)

    units = synthesize_top_filers(base_calibrated, pareto_alpha=alpha)
    if enrich:
        units = enrich_for_credits(units)
    units, _ = anchor_tail_gains(units)             # the $1M+ class's Table 21 gains, before tail_k
    units, tail_k = calibrate_synthetic_tail_to_tax_target(
        score(units), score=score, scale_gains=False)
    units = score(age_synthetic_tail(units, spec=spec))
    return units, tail_k


def project_units(
    units: pd.DataFrame,
    *,
    year: int,
    spec: AgingSpec,
    macro_shock: Optional[str] = None,
) -> pd.DataFrame:
    """Project ``units`` to tax year ``year`` and re-score what moved
    (``projection.engine.project_units``: the engine, the top-share drift
    above $500K, the recession shock, ``refresh_stale_hawaii_tax``)."""
    from tax_modeler.projection import engine

    return engine.project_units(units, year, spec, macro_shock=macro_shock)


def statute(factory: Callable[[int], "TaxSystemConfig"]) -> Callable[[int], "TaxSystemConfig"]:
    """``factory`` with the statutory alternative tax on capital gains
    (``cg_alt_tax="statute"``) instead of the registry's stacked shortcut."""
    return lambda year: replace(factory(year), cg_alt_tax="statute")


def mid_gains_factors(
    base_calibrated: pd.DataFrame,
    *,
    alpha: float,
    spec: AgingSpec,
    ded_params,
    cal_tax_year: int,
    years,
) -> dict[int, dict[str, float]]:
    """The DOTAX anchoring's scale factor per class (``cg_anchor.anchor_factors``)
    on the MID population (``alpha``/``spec`` are MID's), per tax year.
    Every scenario's gains are rescaled with these, so DOTAX anchors MID and
    the other scenarios' gains move with their own top incomes."""
    from tax_modeler.calibration.cg_anchor import anchor_factors

    units, _ = build_units(base_calibrated, alpha=alpha, spec=spec,
                           ded_params=ded_params, cal_tax_year=cal_tax_year)
    return {int(y): anchor_factors(project_units(units, year=y, spec=spec), y)
            for y in years}
