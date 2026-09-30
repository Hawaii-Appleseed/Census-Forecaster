"""The population the Act 24 estimates are scored on.

``forecast_sb3125_enhanced.py`` (the Act 24 page) and the tax simulator's
scoring population (``tax_modeler.simulator.population``) both build their
tax units here, so the simulator scores exactly the population the published
estimates use.

Two steps:

* :func:`build_units` — the calibrated TY2023 base plus the synthetic $1M+
  tail (Pareto ``alpha``), capital-gains shares, and the tail calibrated to
  the DOTAX TY2022 $1M+ tax target (iterated until re-scoring lands on it)
  and then aged to the PUMS income dollar year with the scenario's
  top-income premium, re-scored on the calibration's deduction basis.
* :func:`project_units` — one tax year: county income growth, the top-income
  growth premium, an optional recession shock, then the tax of every unit
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


def build_units(
    base_calibrated: pd.DataFrame,
    *,
    alpha: float,
    top_premium: float,
    ded_params,
    cal_tax_year: int,
    enrich: bool = False,
) -> tuple[pd.DataFrame, float]:
    """Synthesize the $1M+ tail on the calibrated base; return (units, tail_k).

    The tail's incomes are scaled until its re-scored tax is DOTAX's TY2022
    $1M+ tax (``calibrate_synthetic_tail_to_tax_target``; ``tail_k`` is that
    scale), then aged from TY2022 to the PUMS income dollar year
    (``age_synthetic_tail``), since :func:`project_units` grows every unit
    from there. The aging includes 2022-2024 of the top-income premium, so
    ``top_premium`` must be the one the scenario projects with.

    ``ded_params`` / ``cal_tax_year`` are the deduction params and vintage the
    base was calibrated under; re-scoring on any other basis derives a wrong
    ``tail_k``. ``enrich`` re-runs ``enrich_for_credits`` after synthesis, as
    the distributional pass does: it fills the synthetic units'
    ``total_cash_income`` (used to rank households) and leaves every tax input
    unchanged.
    """
    from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
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
    units = impute_capital_gains_from_soi(units)    # CG rate cap for $100K-$1M filers
    units, tail_k = calibrate_synthetic_tail_to_tax_target(score(units), score=score)
    units = score(age_synthetic_tail(units, top_premium=top_premium))
    return units, tail_k


def project_units(
    units: pd.DataFrame,
    *,
    year: int,
    top_premium: float,
    macro_shock: Optional[str] = None,
) -> pd.DataFrame:
    """Project ``units`` to tax year ``year`` and re-score what moved.

    County B19013 growth with per-filer effective deductions, then the
    top-income growth premium (filers above $500K), then the recession shock
    (``macro_shock``; None for the baseline macro path). The premium blanks
    the tax columns of the units it rescales and the shock rescales every
    unit, so both are followed by ``refresh_stale_hawaii_tax``: left blank,
    every filer above $500K was scored on the standard deduction alone.
    """
    from tax_modeler.projection.tax_unit_projector import (
        project_tax_units_forward,
        refresh_stale_hawaii_tax,
    )
    from tax_modeler.scenarios.behavioral_response import apply_top_income_growth_premium
    from tax_modeler.scenarios.macro_scenarios import apply_macro_recession_shock

    projected = project_tax_units_forward(units, target_year=year, method="ensemble")
    projected = apply_top_income_growth_premium(
        projected, target_year=year, annual_premium=top_premium)
    if macro_shock is not None:
        projected = apply_macro_recession_shock(projected, target_year=year, scenario=macro_shock)
    return refresh_stale_hawaii_tax(projected, target_year=year)


def statute(factory: Callable[[int], "TaxSystemConfig"]) -> Callable[[int], "TaxSystemConfig"]:
    """``factory`` with the statutory alternative tax on capital gains
    (``cg_alt_tax="statute"``) instead of the registry's stacked shortcut."""
    return lambda year: replace(factory(year), cg_alt_tax="statute")


def mid_gains_factors(
    base_calibrated: pd.DataFrame,
    *,
    alpha: float,
    top_premium: float,
    ded_params,
    cal_tax_year: int,
    years,
) -> dict[int, dict[str, float]]:
    """The DOTAX anchoring's scale factor per class (``cg_anchor.anchor_factors``)
    on the MID population (``alpha``/``top_premium`` are MID's), per tax year.
    Every scenario's gains are rescaled with these, so DOTAX anchors MID and
    the other scenarios' gains move with their own top incomes."""
    from tax_modeler.calibration.cg_anchor import anchor_factors

    units, _ = build_units(base_calibrated, alpha=alpha, top_premium=top_premium,
                           ded_params=ded_params, cal_tax_year=cal_tax_year)
    return {int(y): anchor_factors(project_units(units, year=y, top_premium=top_premium), y)
            for y in years}
