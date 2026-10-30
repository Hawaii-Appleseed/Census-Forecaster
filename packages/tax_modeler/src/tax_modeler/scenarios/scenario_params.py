"""Typed scenario-parameter dataclasses (Phase 11).

Before Phase 11, scenario knobs scattered across forecast scripts as
loose constants:

  forecast_sb3125_cd1_enhanced.py
      MID_ALPHA = 1.5
      MID_TOP_PREMIUM = 0.010
      SCENARIOS = [{"label": "LOW", "alpha": 1.7, "reec": "obbba_severe", ...}, ...]

  forecast_sb3125_cd1_sensitivity.py
      SCENARIOS = [("LOW", 1.6, "obbba_severe"), ...]

This module centralizes them in three typed dataclasses with named
constructors for low/mid/high preset values:

  * :class:`TopIncomeParams` — synthesis knobs (Pareto α, top premium,
    top-bracket differential)
  * :class:`MacroScenario` — credit-overlay + macro-shock knobs (REEC
    demand, CGEC growth, macro shock label, COR scale factor)
  * :class:`ForecastScenario` — bundles ``TopIncomeParams``,
    ``BehavioralParams`` (existing), and ``MacroScenario`` into one
    stable identifier with a label and metadata.

The existing :class:`tax_modeler.scenarios.behavioral_response.BehavioralParams`
(elasticity bundle: ETI, migration, PTE capture) stays as-is and is
held by ``ForecastScenario.behavior``.

Usage::

    from tax_modeler import ForecastScenario, BehavioralParams

    s = ForecastScenario.mid()
    print(s.top.pareto_alpha)       # 1.5
    print(s.macro.reec_demand_scenario)  # 'obbba_mid'
    print(s.behavior.eti)           # 0.40

Forecast scripts can now write::

    for s in [ForecastScenario.low(), ForecastScenario.mid(), ForecastScenario.high()]:
        run_one_scenario(s)

instead of unpacking opaque tuples.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from tax_modeler.errors import ConfigError

from .behavioral_response import BehavioralParams

# ---------------------------------------------------------------------------
# Top-income synthesis parameters
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TopIncomeParams:
    """Top-income / Pareto synthesis knobs.

    These control how the simulator extrapolates the top of the income
    distribution from PUMS (which is top-coded around $1M). Lower
    ``pareto_alpha`` → heavier tail → more $5M+ filers → larger
    top-bracket impact. ``top_premium_pct`` is the annual additional
    growth rate applied to filers above ``top_premium_threshold``.

    Attributes
    ----------
    pareto_alpha:
        Shape parameter for the synthesized $1M+ tail. IRS SOI 2022 HI
        empirical estimate: ~1.5. Conservative (revenue): 1.6+. Heavy
        (more top revenue): 1.4 or below.
    top_premium_pct:
        Annual growth premium applied to filers above
        ``top_premium_threshold``. IRS SOI empirical: ~1.3pp/yr for HI
        $500K+ filers (2014-2022).
    top_premium_threshold:
        AGI threshold above which the premium applies. Default $500K
        per IRS SOI binning.
    top_bracket_differential:
        Additional %-pt growth for filers above the top legislated
        bracket (typically $1M+). Used in projection ensemble.
    """

    pareto_alpha: float
    top_premium_pct: float
    top_premium_threshold: float = 500_000.0
    top_bracket_differential: float = 0.025

    @classmethod
    def low(cls) -> TopIncomeParams:
        """Conservative for revenue (light tail, low premium)."""
        return cls(pareto_alpha=1.6, top_premium_pct=0.005,
                   top_bracket_differential=0.015)

    @classmethod
    def mid(cls) -> TopIncomeParams:
        """Calibrated MID — IRS SOI 2022 HI tail shape."""
        return cls(pareto_alpha=1.5, top_premium_pct=0.010,
                   top_bracket_differential=0.025)

    @classmethod
    def high(cls) -> TopIncomeParams:
        """Aggressive for revenue (heavy tail, high premium)."""
        return cls(pareto_alpha=1.4, top_premium_pct=0.013,
                   top_bracket_differential=0.035)

    @classmethod
    def named(cls, name: str) -> TopIncomeParams:
        m = {"low": cls.low, "mid": cls.mid, "high": cls.high}
        if name not in m:
            raise ConfigError(
                f"Unknown TopIncomeParams scenario {name!r}",
                available=sorted(m),
            )
        return m[name]()


# ---------------------------------------------------------------------------
# Macro / credit-overlay scenario
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MacroScenario:
    """Macro + bill-specific credit-overlay scenario.

    Attributes
    ----------
    reec_demand_scenario:
        REEC (Renewable Energy Equipment Credit) post-OBBBA demand
        regime. Valid values: ``"pre_obbba"`` (no decay, full $99.6M
        baseline), ``"obbba_mid"`` (SEIA-tempered, default), ``"obbba_severe"``
        (SEIA national applied to HI). Drives ``compute_credit_overlay``.
    cgec_growth_pct:
        Annual real growth rate assumed for CGEC (Capital Goods Excise
        Credit) outlays before the §235-110.7 sunset (12/31/2027).
    macro_shock:
        Label for an embedded macro shock scenario:
        ``"none"``, ``"recession_2027_mild"``, ``"recession_2027_severe"``.
        Drives growth-rate adjustments in the ensemble projector.
    cor_scale_factor:
        Multiplier applied to the microsim baseline to align with
        Council on Revenues March-2026 projections. Default 1.0 (raw
        microsim).
    reec_eff_share:
        Fraction of REEC outlays that effectively reduce state
        revenue (the rest is federal pass-through). Default 0.30.
    """

    reec_demand_scenario: str
    cgec_growth_pct: float = 0.025
    macro_shock: str = "none"
    cor_scale_factor: float = 1.0
    reec_eff_share: float = 0.30

    @classmethod
    def low(cls) -> MacroScenario:
        return cls(reec_demand_scenario="obbba_severe",
                   cgec_growth_pct=0.015, macro_shock="recession_2027_mild")

    @classmethod
    def mid(cls) -> MacroScenario:
        return cls(reec_demand_scenario="obbba_mid",
                   cgec_growth_pct=0.025, macro_shock="none")

    @classmethod
    def high(cls) -> MacroScenario:
        return cls(reec_demand_scenario="pre_obbba",
                   cgec_growth_pct=0.035, macro_shock="none")

    @classmethod
    def named(cls, name: str) -> MacroScenario:
        m = {"low": cls.low, "mid": cls.mid, "high": cls.high}
        if name not in m:
            raise ConfigError(
                f"Unknown MacroScenario {name!r}", available=sorted(m),
            )
        return m[name]()


# ---------------------------------------------------------------------------
# Forecast scenario bundle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ForecastScenario:
    """Composite scenario covering top-income synthesis, behavioral
    response, and macro/credit-overlay knobs.

    A single :class:`ForecastScenario` replaces the loose tuple/dict
    structures previously used in ``forecast_*.py`` sensitivity sweeps.

    Attributes
    ----------
    label:
        Display label, e.g. ``"LOW"``, ``"MID"``, ``"HIGH"``,
        ``"VERY_HIGH"``. Used in summary tables.
    top:
        :class:`TopIncomeParams` for top-income synthesis.
    behavior:
        :class:`BehavioralParams` (ETI, migration, PTE capture).
    macro:
        :class:`MacroScenario` for credit-overlay + macro-shock knobs.
    metadata:
        Free-form ``dict`` for citations, authorship, dates. Phase 11
        convention is to populate ``"created"``, ``"author"``, and
        ``"data_vintage"``.
    """

    label: str
    top: TopIncomeParams
    behavior: BehavioralParams
    macro: MacroScenario
    metadata: dict = field(default_factory=dict)

    @classmethod
    def low(cls) -> ForecastScenario:
        return cls(
            label="LOW",
            top=TopIncomeParams.low(),
            behavior=BehavioralParams.high(),    # weak revenue, strong behavior
            macro=MacroScenario.low(),
        )

    @classmethod
    def mid(cls) -> ForecastScenario:
        return cls(
            label="MID",
            top=TopIncomeParams.mid(),
            behavior=BehavioralParams.mid(),
            macro=MacroScenario.mid(),
        )

    @classmethod
    def high(cls) -> ForecastScenario:
        return cls(
            label="HIGH",
            top=TopIncomeParams.high(),
            behavior=BehavioralParams.low(),     # strong revenue, weak behavior
            macro=MacroScenario.high(),
        )

    @classmethod
    def static(cls) -> ForecastScenario:
        """Static-scoring scenario (no behavioral response)."""
        return cls(
            label="STATIC",
            top=TopIncomeParams.mid(),
            behavior=BehavioralParams.static(),
            macro=MacroScenario.mid(),
        )

    @classmethod
    def named(cls, name: str) -> ForecastScenario:
        m = {"low": cls.low, "mid": cls.mid, "high": cls.high, "static": cls.static}
        if name.lower() not in m:
            raise ConfigError(
                f"Unknown ForecastScenario {name!r}",
                available=sorted(m),
            )
        return m[name.lower()]()


__all__ = [
    "TopIncomeParams",
    "MacroScenario",
    "ForecastScenario",
]
