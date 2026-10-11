"""The one income-aging engine every Act 24 path projects through.

Until October 2026 the headline path (``forecast_sb3125_enhanced.py``, the
Act 24 page, the tax simulator) aged every income source by the county
B19013 median scalar (``project_tax_units_forward(method="ensemble")``),
while ``forecast_sb3125_vs_fy26base.py``, ``forecast_cg_rate_options.py``
and ``forecast_working_family_credits.py`` aged by CBO component
(``cbo_aging.age_filers_with_components`` through
``year_recalibrator.project_and_recalibrate``). The DOTAX scorecard
(``DOTAX_SCORECARD.md``) showed the median scalar is the wrong carrier for the
top class, and the four scripts could drift because each spelled out its own
projection. They now all call :func:`project_units` (directly, or through
``project_and_recalibrate``, whose aging step is :func:`age_incomes`), with an
:class:`AgingSpec` saying which engine and which top-share drift.

Engines
-------
``"cbo"`` (default)
    Per-source aging: each unit's wages, business, capital gains, dividends,
    interest, retirement and other income grow at the CBO Outlook rate for
    that source times the Hawaiʻi factor (``cbo_aging.DEFAULT_HAWAII_FACTORS``),
    from the PUMS income dollar year. Composition drives the top: a unit whose
    income is 40% gains grows faster than a wage earner because CBO grows
    gains faster than wages, not because of a premium.
``"scalar"``
    The old path, kept for comparison: every source in a county grows by the
    county's projected B19013 median (``census_forecaster`` ensemble), the
    Honolulu proxy where a county is missing.

Top-share drift
---------------
One named parameter, ``AgingSpec.top_share_drift``, per year above
``drift_threshold`` from ``drift_base_year`` (the PUMS dollar year), applied
after the engine (``behavioral_response.apply_top_income_growth_premium`` is
the mechanics). It replaces a stack that was never jointly backtested
(FORECAST_ASSESSMENT_2026-06-11.md §5): the +0.3/1.0/2.3%/yr premium above
$500K (whose MID carried a 0.8pp "outmigration haircut" on top of the explicit
migration elasticity, a double count), the +2.5%/yr top-bracket differential
in the forward filer counts (``forward_targets.build_targets(top_differential)``,
now this parameter), the 0.5%/yr ``rate_drift`` on the forward effective
rates (now 0), and, for the gains base, the CBO x 0.85 growth
(``cg_anchor.cg_growth``, now the windowed-share null). The values are in
:data:`TOP_SHARE_DRIFT` with their source.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

import pandas as pd

from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR

AGING_ENGINES = ("cbo", "scalar")

TOP_SHARE_DRIFT: dict[str, float] = {"LOW": -0.010, "MID": 0.0, "HIGH": 0.010}
"""Top-share drift per scenario, per year above $500K, on top of CBO component aging.

Source. (1) DOTAX Table A-8, TY2019-2023 (``data/calibration/dotax_a8_top_class.json``):
the $1M+ class's share of resident tax before credits was 16.8%, 16.1%, 25.3%,
21.9% and 14.9%; the fitted trend in its log is +0.7%/yr with a standard error of
8.2%/yr. Its share of *returns* trends +10.9%/yr (SE 6.1%), but that is nominal
income carrying filers across the fixed $1M line, which the aging does
mechanically; the tax share is the drift's own margin. Five points support no
drift of either sign (``scripts/dotax_scorecard.py`` prints the series and the
sweep). (2) IRS SOI 2012-2019, national: $500K+ AGI grew about
1.8pp/yr faster than the median. Under CBO component aging most of that is
composition (CBO grows gains 5.5%/yr and dividends 4.5% against wages 4.4% x
the 0.85 Hawaiʻi factor, so a tail that is 40% gains already outgrows a wage
earner by about 0.9pp/yr); the residual the drift would carry is about 1pp.
(3) The explicit migration response (``behavioral_response``, 0.001-0.01 per
point) is the only place out-migration enters: no haircut here.

MID 0: the Hawaiʻi series supports none. HIGH +1.0%/yr: the national residual
after composition, as the upper band. LOW -1.0%/yr: symmetric, and the sign
of TY2021-2023. Not a forecast of the next five years; a band around the one
value the data support.
"""


@dataclass(frozen=True)
class AgingSpec:
    """How a population is aged to a tax year.

    ``engine``: ``"cbo"`` or ``"scalar"`` (module doc). ``top_share_drift``:
    per year above ``drift_threshold`` from ``drift_base_year``. ``cbo_vintage``
    and ``hawaii_factors`` (None: ``cbo_aging.DEFAULT_HAWAII_FACTORS``) feed the
    CBO engine; ``method`` the scalar engine's county projector.
    """

    engine: str = "cbo"
    top_share_drift: float = 0.0
    drift_threshold: float = 500_000.0
    drift_base_year: int = PUMS_INCOME_DOLLAR_YEAR
    cbo_vintage: str = "2025-01"
    hawaii_factors: Mapping[str, float] | None = None
    method: str = "ensemble"

    def __post_init__(self) -> None:
        if self.engine not in AGING_ENGINES:
            raise ValueError(f"engine must be one of {AGING_ENGINES}, not {self.engine!r}")

    def with_drift(self, drift: float) -> AgingSpec:
        return replace(self, top_share_drift=float(drift))

    @classmethod
    def for_scenario(cls, label: str, *, engine: str = "cbo", **kw) -> AgingSpec:
        """The spec for an Act 24 scenario label (``TOP_SHARE_DRIFT``)."""
        return cls(engine=engine, top_share_drift=TOP_SHARE_DRIFT[label.upper()], **kw)

    def describe(self) -> str:
        return (f"{self.engine} aging, top_share_drift={self.top_share_drift:+.1%}/yr "
                f"above ${self.drift_threshold:,.0f} from {self.drift_base_year}")


DEFAULT_SPEC = AgingSpec()


def age_incomes(units: pd.DataFrame, year: int, spec: AgingSpec = DEFAULT_SPEC,
                *, base_year: int | None = None, **projector_kw) -> pd.DataFrame:
    """Age ``units``' incomes to ``year`` with ``spec.engine`` and re-score tax
    and federal credits (``project_tax_units_forward``). No drift, no shock:
    the step ``project_and_recalibrate`` re-rakes after."""
    from tax_modeler.projection.tax_unit_projector import project_tax_units_forward

    if spec.engine == "cbo":
        return project_tax_units_forward(
            units, target_year=year, use_cbo_aging=True, cbo_vintage=spec.cbo_vintage,
            cbo_hawaii_factors=dict(spec.hawaii_factors) if spec.hawaii_factors else None,
            **projector_kw)
    return project_tax_units_forward(units, target_year=year, base_year=base_year,
                                     method=spec.method, **projector_kw)


def apply_top_share_drift(df: pd.DataFrame, year: int, spec: AgingSpec = DEFAULT_SPEC) -> pd.DataFrame:
    """Scale incomes above the threshold by ``(1 + drift) ** (year - base)``;
    blanks the tax columns it touches (re-score with ``refresh_stale_hawaii_tax``)."""
    from tax_modeler.scenarios.behavioral_response import apply_top_income_growth_premium

    return apply_top_income_growth_premium(
        df, target_year=year, base_year=spec.drift_base_year,
        annual_premium=spec.top_share_drift, threshold=spec.drift_threshold)


def project_units(units: pd.DataFrame, year: int, spec: AgingSpec = DEFAULT_SPEC,
                  *, macro_shock: str | None = None, **projector_kw) -> pd.DataFrame:
    """Project ``units`` to tax year ``year``: the engine, the top-share drift,
    the optional recession shock, then re-score every unit whose income the last
    two steps changed (``refresh_stale_hawaii_tax``). The shared entry point."""
    from tax_modeler.projection.tax_unit_projector import refresh_stale_hawaii_tax
    from tax_modeler.scenarios.macro_scenarios import apply_macro_recession_shock

    projected = age_incomes(units, year, spec, **projector_kw)
    projected = apply_top_share_drift(projected, year, spec)
    if macro_shock is not None:
        projected = apply_macro_recession_shock(projected, target_year=year, scenario=macro_shock)
    return refresh_stale_hawaii_tax(projected, target_year=year)


def component_growth(spec: AgingSpec, from_year: int, to_year: int) -> dict[str, float]:
    """Net growth factor per CBO component from ``from_year`` to ``to_year``
    (CBO x Hawaiʻi factor on the increment, ``cbo_aging.net_growth_factor``)."""
    from tax_modeler.calibration.cbo_aging import (
        CBO_COMPONENTS,
        DEFAULT_HAWAII_FACTORS,
        load_cbo_rates,
        net_growth_factor,
    )

    rates = load_cbo_rates(vintage=spec.cbo_vintage)
    hi = dict(DEFAULT_HAWAII_FACTORS if spec.hawaii_factors is None else spec.hawaii_factors)
    return {c: net_growth_factor(rates.growth(c, to_year, from_year), hi.get(c, 1.0))
            for c in CBO_COMPONENTS if c != "proprietors"}


def tail_growth_factor(spec: AgingSpec, from_year: int, to_year: int,
                       cg_share: float = 0.0) -> float:
    """Growth of a synthetic $1M+ unit from ``from_year`` to ``to_year`` under
    ``spec``: wages ``1 - cg_share`` and gains ``cg_share`` at their component
    rates (CBO engine), or Honolulu's observed B19013 growth (scalar engine, as
    the old tail aging), times the drift over the same years.

    The tail is calibrated to a DOTAX tax year and the projection grows every
    unit from the PUMS dollar year, so the tail has to be carried between the
    two by the same engine the projection uses (``top_income_synthesis.age_synthetic_tail``).
    """
    years = to_year - from_year
    drift = (1.0 + spec.top_share_drift) ** years
    if years == 0:
        return 1.0
    if spec.engine == "cbo":
        g = component_growth(spec, from_year, to_year)
        return ((1.0 - cg_share) * g["wages"] + cg_share * g["capital_gains"]) * drift
    from tax_modeler.scenarios.top_income_synthesis import _observed_honolulu_b19013

    return _observed_honolulu_b19013(to_year) / _observed_honolulu_b19013(from_year) * drift


__all__ = ["AGING_ENGINES", "TOP_SHARE_DRIFT", "AgingSpec", "DEFAULT_SPEC", "age_incomes",
           "apply_top_share_drift", "project_units", "component_growth", "tail_growth_factor"]
