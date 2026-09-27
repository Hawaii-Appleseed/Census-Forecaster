"""The population the Act 24 estimates are scored on.

``forecast_sb3125_enhanced.py`` (the Act 24 page) and the tax simulator's
scoring population (``tax_modeler.simulator.population``) both build their
tax units here, so the simulator scores exactly the population the published
estimates use.

Two steps:

* :func:`build_units` — the calibrated TY2023 base plus the synthetic $1M+
  tail (Pareto ``alpha``), capital-gains shares, and the tail rescaled to the
  DOTAX $1M+ tax target, re-scored on the calibration's deduction basis.
* :func:`project_units` — one tax year: county income growth, the top-income
  growth premium, an optional recession shock, then the tax of every unit
  whose income those steps changed is recomputed.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def build_units(
    base_calibrated: pd.DataFrame,
    *,
    alpha: float,
    ded_params,
    cal_tax_year: int,
    enrich: bool = False,
) -> tuple[pd.DataFrame, float]:
    """Synthesize the $1M+ tail on the calibrated base; return (units, tail_k).

    ``ded_params`` / ``cal_tax_year`` are the deduction params and vintage the
    base was calibrated under; re-scoring on any other basis derives a wrong
    ``tail_k``. ``enrich`` re-runs ``enrich_for_credits`` after synthesis, as
    the distributional pass does: it fills the synthetic units'
    ``total_cash_income`` (used to rank households) and leaves every tax input
    unchanged.
    """
    from tax_modeler.calibration.cg_imputation import impute_capital_gains_from_soi
    from tax_modeler.pipeline import _compute_base_tax, _enrich_for_credits
    from tax_modeler.scenarios.top_income_synthesis import (
        rescale_synthetic_tail_to_tax_target,
        synthesize_top_filers,
    )

    units = synthesize_top_filers(base_calibrated, pareto_alpha=alpha)
    if enrich:
        units = _enrich_for_credits(units)
    units = impute_capital_gains_from_soi(units)    # CG rate cap for $100K-$1M filers
    units = _compute_base_tax(units, deduction_params=ded_params, tax_year=cal_tax_year)
    units, tail_k = rescale_synthetic_tail_to_tax_target(units)
    units = _compute_base_tax(units, deduction_params=ded_params, tax_year=cal_tax_year)
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
