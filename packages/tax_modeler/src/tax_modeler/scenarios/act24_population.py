"""The population the Act 24 estimates are scored on.

``chain/forecast_sb3125_enhanced.py`` (the Act 24 page) and the tax simulator's
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

:class:`CalibratedBase` carries the calibrated base together with what every
call above needs alongside it (its deduction params and vintage, and the
chain's population store), so a chain step loads it once and asks it for
units and projections.
"""
from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

import pandas as pd

if TYPE_CHECKING:
    from tax_modeler.config.tax_system_config import TaxSystemConfig
    from tax_modeler.scenarios.population_store import PopulationStore

# The MID scenario's population assumptions: the Pareto alpha of the
# synthetic $1M+ tail and the top-income growth premium (per year). Every
# chain step that scores "the MID population" uses these.
MID_ALPHA = 1.5
MID_TOP_PREMIUM = 0.010


@dataclass(frozen=True)
class CalibratedBase:
    """The calibrated base (``artifacts.load_calibrated_base``), the deduction
    params and tax year it was calibrated under, and the population store
    that loaded it (None: nothing is shared or cached)."""

    frame: pd.DataFrame
    ded_params: Any
    tax_year: int
    store: PopulationStore | None = None

    @classmethod
    def load(cls, path, store: PopulationStore | None = None) -> CalibratedBase:
        """Load the pickle at ``path``, through ``store`` when given."""
        if store is not None:
            frame, ded_params, meta = store.base(path)
        else:
            from tax_modeler.artifacts import load_calibrated_base

            frame, ded_params, meta = load_calibrated_base(path)
        return cls(frame, ded_params, int(meta.get("tax_year", 2023)), store)

    def units(self, *, alpha: float, top_premium: float,
              enrich: bool = False) -> tuple[pd.DataFrame, float]:
        """:func:`build_units` on this base: ``(units, tail_k)``."""
        return build_units(self.frame, alpha=alpha, top_premium=top_premium,
                           ded_params=self.ded_params, cal_tax_year=self.tax_year,
                           enrich=enrich, store=self.store)

    def projected(self, units: pd.DataFrame, *, year: int, top_premium: float,
                  macro_shock: str | None = None,
                  gains_factors: dict[str, float] | None = None) -> pd.DataFrame:
        """:func:`project_units`, then the gains rescaled with
        ``gains_factors`` (one year's ``cg_anchor`` factors) when given."""
        projected = project_units(units, year=year, top_premium=top_premium,
                                  macro_shock=macro_shock, store=self.store)
        if gains_factors is None:
            return projected
        from tax_modeler.calibration.cg_anchor import apply_anchor_factors

        return apply_anchor_factors(projected, gains_factors)

    def gains_factors(self, *, years: Iterable[int], alpha: float,
                      top_premium: float) -> dict[int, dict[str, float]]:
        """:func:`mid_gains_factors` on this base."""
        return mid_gains_factors(self.frame, alpha=alpha, top_premium=top_premium,
                                 ded_params=self.ded_params, cal_tax_year=self.tax_year,
                                 years=years, store=self.store)


def build_units(
    base_calibrated: pd.DataFrame,
    *,
    alpha: float,
    top_premium: float,
    ded_params,
    cal_tax_year: int,
    enrich: bool = False,
    store=None,
) -> tuple[pd.DataFrame, float]:
    """Synthesize the $1M+ tail on the calibrated base; return (units, tail_k).

    With a ``store`` (:class:`~tax_modeler.scenarios.population_store.PopulationStore`)
    that loaded ``base_calibrated``, the result is computed once per store
    and loaded after that.

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
    from tax_modeler.pipeline import compute_base_tax

    def score(u: pd.DataFrame) -> pd.DataFrame:
        return compute_base_tax(u, deduction_params=ded_params, tax_year=cal_tax_year)

    def build() -> tuple[pd.DataFrame, float]:
        return _build_units(base_calibrated, alpha=alpha, top_premium=top_premium,
                            enrich=enrich, score=score)

    if store is not None and store.name_of(base_calibrated) == "base":
        from tax_modeler.scenarios.population_store import params_tag

        name = (f"units-a{alpha!r}-p{top_premium!r}-e{int(enrich)}"
                f"-ty{cal_tax_year}-d{params_tag(ded_params)}")
        return store.get(name, build)
    return build()


def _build_units(base_calibrated, *, alpha, top_premium, enrich, score):
    from tax_modeler.base import synthesize_calibrated_tail
    from tax_modeler.scenarios.top_income_synthesis import age_synthetic_tail

    units, tail_k = synthesize_calibrated_tail(
        base_calibrated, alpha=alpha, score=score, enrich=enrich)
    units = score(age_synthetic_tail(units, top_premium=top_premium))
    return units, tail_k


def project_units(
    units: pd.DataFrame,
    *,
    year: int,
    top_premium: float,
    macro_shock: str | None = None,
    store=None,
) -> pd.DataFrame:
    """Project ``units`` to tax year ``year`` and re-score what moved.

    With a ``store`` that produced ``units`` (``build_units(..., store=)``),
    each projection is computed once per store and loaded after that.

    County B19013 growth with per-filer effective deductions, then the
    top-income growth premium (filers above $500K), then the recession shock
    (``macro_shock``; None for the baseline macro path). The premium blanks
    the tax columns of the units it rescales and the shock rescales every
    unit, so both are followed by ``refresh_stale_hawaii_tax``: left blank,
    every filer above $500K was scored on the standard deduction alone.
    """
    def project() -> pd.DataFrame:
        return _project_units(units, year=year, top_premium=top_premium, macro_shock=macro_shock)

    units_name = store.name_of(units) if store is not None else None
    if units_name is not None:
        return store.get(f"{units_name}-y{year}-p{top_premium!r}-s{macro_shock}", project)
    return project()


def _project_units(units, *, year, top_premium, macro_shock):
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


def statute(factory: Callable[[int], TaxSystemConfig]) -> Callable[[int], TaxSystemConfig]:
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
    store=None,
) -> dict[int, dict[str, float]]:
    """The DOTAX anchoring's scale factor per class (``cg_anchor.anchor_factors``)
    on the MID population (``alpha``/``top_premium`` are MID's), per tax year.
    Every scenario's gains are rescaled with these, so DOTAX anchors MID and
    the other scenarios' gains move with their own top incomes."""
    from tax_modeler.calibration.cg_anchor import anchor_factors
    from tax_modeler.parallel import fork_map

    units, _ = build_units(base_calibrated, alpha=alpha, top_premium=top_premium,
                           ded_params=ded_params, cal_tax_year=cal_tax_year, store=store)
    years = [int(y) for y in years]
    # Each year projects the same units on its own, so the years run in
    # forked workers (which save their projections to ``store``, if given).
    factors = fork_map(
        lambda y: anchor_factors(
            project_units(units, year=y, top_premium=top_premium, store=store), y), years)
    return dict(zip(years, factors, strict=True))
