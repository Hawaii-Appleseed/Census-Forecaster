"""Year-forward AGI mass targets per AGI bracket and SOI tier (Hawaii residents).

Constructs aggregate AGI-mass targets ($M) per AGI bracket for each forecast
year (TY2025-2031). Used by the stage-2 reweighting step in
``simultaneous_calibrator`` to align CBO-aged filer incomes to a forward
distributional target, instead of letting cohort-level CBO aging compound
indefinitely (a known mis-specification — see Auten/Gee/Turner 2013 on
top-1% mobility, CBO 2018 microsim overview, TPC 2013 tech doc).

Methodology (Hybrid: DOTAX bin shape × CBO total)
-------------------------------------------------
1. **Baseline (DOTAX, ``dotax_base.BASE_YEAR`` = TY2023)**: HI resident AGI
   per AGI bracket from DOTAX *Individual Income Tax Statistics*, Table A-1
   (resident taxable returns). The ``$400K+`` aggregate ($9,319M, 8,917 filers
   in TY2023) is subdivided into the 4 finer DOTAX brackets ($400K-$500K,
   $500K-$750K, $750K-$1M, $1M+) in proportion to each bracket's A-8 tax before
   credits over its effective rate on Hawaiʻi AGI (``dotax_base.agi_targets_M``).
   TY2022's split used filer-count proportions × SOI Table 1.4 average AGI.

2. **$1M+ tier breakdown**: 5 tiers from SOI Table 1.4 ($1M-$1.5M,
   $1.5M-$2M, $2M-$5M, $5M-$10M, $10M+). HI's $1M+ filers (1,704 in TY2023)
   distributed proportionally across tiers using national tier counts;
   tier avg AGI from SOI Table 1.4. Tier AGI mass = HI tier count ×
   tier avg AGI.

3. **Forward growth**: Apply CBO Outlook 2025-01 component growth rates,
   weighted by per-bracket income composition (the same per-component
   factors used by ``cbo_aging.age_filers_with_components``). Composition
   shares come from SOI Table 1.4 sub-bins for $200K+ brackets and from
   PUMS observed averages for sub-$200K. HI calibration factors
   (``DEFAULT_HAWAII_FACTORS``) apply.

4. **Filer count migration**: Already handled by ``forward_targets.py``;
   reused so the AGI targets and filer targets stay consistent.

Outputs
-------
- ``bracket_agi_targets``: ``Dict[(agi_lo, agi_hi), $M]`` for the 15 DOTAX
  brackets (or 14 brackets + 5 SOI tiers when ``include_soi_tiers=True``).
- ``tier_agi_targets``: ``Dict[(tier_lo, tier_hi), $M]`` for the 5 SOI
  $1M+ tiers (only when ``include_soi_tiers=True``).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from tax_modeler.calibration import dotax_base
from tax_modeler.calibration.cbo_aging import net_growth_factor

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Baseline AGI by bracket: ``dotax_base.agi_targets_M(year)`` (Table A-1)
# ---------------------------------------------------------------------------

# Bracket-level CBO aging compositions (rough average shares by income bracket).
# Anchored on SOI Table 1.4 national bin compositions for $200K+, and BEA
# state-level shares for HI sub-$200K. These are NOT per-filer compositions —
# just bracket-level composition averages used to derive an AGI growth factor.
#
# Components: wages, business, capital_gains, dividends, interest, retirement, other
_BRACKET_COMPOSITIONS: Dict[Tuple[float, float], Dict[str, float]] = {
    (0,         10_000):    {"wages": 0.45, "business": 0.05, "capital_gains": 0.00,
                             "dividends": 0.01, "interest": 0.02, "retirement": 0.30, "other": 0.17},
    (10_000,    20_000):    {"wages": 0.50, "business": 0.05, "capital_gains": 0.00,
                             "dividends": 0.01, "interest": 0.02, "retirement": 0.32, "other": 0.10},
    (20_000,    30_000):    {"wages": 0.62, "business": 0.05, "capital_gains": 0.00,
                             "dividends": 0.01, "interest": 0.02, "retirement": 0.22, "other": 0.08},
    (30_000,    40_000):    {"wages": 0.70, "business": 0.05, "capital_gains": 0.00,
                             "dividends": 0.01, "interest": 0.02, "retirement": 0.16, "other": 0.06},
    (40_000,    50_000):    {"wages": 0.75, "business": 0.05, "capital_gains": 0.01,
                             "dividends": 0.01, "interest": 0.02, "retirement": 0.12, "other": 0.04},
    (50_000,    75_000):    {"wages": 0.78, "business": 0.05, "capital_gains": 0.01,
                             "dividends": 0.01, "interest": 0.02, "retirement": 0.10, "other": 0.03},
    (75_000,   100_000):    {"wages": 0.78, "business": 0.05, "capital_gains": 0.01,
                             "dividends": 0.02, "interest": 0.02, "retirement": 0.09, "other": 0.03},
    (100_000,  150_000):    {"wages": 0.76, "business": 0.06, "capital_gains": 0.02,
                             "dividends": 0.02, "interest": 0.02, "retirement": 0.09, "other": 0.03},
    (150_000,  200_000):    {"wages": 0.74, "business": 0.07, "capital_gains": 0.03,
                             "dividends": 0.02, "interest": 0.02, "retirement": 0.09, "other": 0.03},
    (200_000,  300_000):    {"wages": 0.70, "business": 0.08, "capital_gains": 0.05,
                             "dividends": 0.03, "interest": 0.02, "retirement": 0.09, "other": 0.03},
    (300_000,  400_000):    {"wages": 0.65, "business": 0.10, "capital_gains": 0.05,
                             "dividends": 0.04, "interest": 0.03, "retirement": 0.10, "other": 0.03},
    (400_000,  500_000):    {"wages": 0.62, "business": 0.11, "capital_gains": 0.05,
                             "dividends": 0.05, "interest": 0.03, "retirement": 0.11, "other": 0.03},
    (500_000,  750_000):    {"wages": 0.58, "business": 0.13, "capital_gains": 0.10,
                             "dividends": 0.05, "interest": 0.03, "retirement": 0.08, "other": 0.03},
    (750_000, 1_000_000):   {"wages": 0.54, "business": 0.14, "capital_gains": 0.11,
                             "dividends": 0.07, "interest": 0.03, "retirement": 0.08, "other": 0.03},
    (1_000_000, np.inf):    {"wages": 0.40, "business": 0.15, "capital_gains": 0.30,
                             "dividends": 0.07, "interest": 0.03, "retirement": 0.03, "other": 0.02},
}

# SOI Table 1.4 tier compositions ($1M+) — finer than the bracket aggregate
_SOI_TIER_COMPOSITIONS: Dict[Tuple[float, float], Dict[str, float]] = {
    (1_000_000, 1_500_000):  {"wages": 0.46, "business": 0.15, "capital_gains": 0.16,
                              "dividends": 0.05, "interest": 0.01, "retirement": 0.04, "other": 0.13},
    (1_500_000, 2_000_000):  {"wages": 0.40, "business": 0.18, "capital_gains": 0.18,
                              "dividends": 0.05, "interest": 0.02, "retirement": 0.03, "other": 0.14},
    (2_000_000, 5_000_000):  {"wages": 0.33, "business": 0.20, "capital_gains": 0.23,
                              "dividends": 0.06, "interest": 0.02, "retirement": 0.02, "other": 0.14},
    (5_000_000, 10_000_000): {"wages": 0.26, "business": 0.20, "capital_gains": 0.30,
                              "dividends": 0.06, "interest": 0.02, "retirement": 0.01, "other": 0.15},
    (10_000_000, np.inf):    {"wages": 0.14, "business": 0.17, "capital_gains": 0.48,
                              "dividends": 0.07, "interest": 0.03, "retirement": 0.00, "other": 0.11},
}


@dataclass(frozen=True)
class ForwardAGITargets:
    """Year-parameterized aggregate-AGI targets for stage-2 reweighting."""
    year: int
    bracket_agi_targets: Dict[Tuple[float, float], float]   # $M per AGI bracket
    tier_agi_targets:    Dict[Tuple[float, float], float]   # $M per SOI tier
    aggregate_agi_M: float


def _component_growth_factor(
    composition: Dict[str, float],
    year: int,
    cbo_rates,
    hawaii_factors: Dict[str, float],
    base_year: int = dotax_base.BASE_YEAR,
) -> float:
    """Composition-weighted CBO growth factor for one bracket/tier.

    Mirrors the formula used by ``cbo_aging.age_filers_with_components`` and
    ``soi_top_anchor._aged_tier_avg_agi`` via the shared
    ``cbo_aging.net_growth_factor`` helper (Hawaii factor applied to the growth
    increment), weighted by composition share and summed. Using the *same*
    convention as the downstream synthesis ensures stage-2 reweighting targets
    are consistent with what the SOI anchor + CBO aging produce, so the IPF
    doesn't fight itself.
    """
    if year == base_year:
        return 1.0
    factor = 0.0
    for component, share in composition.items():
        if share <= 0:
            continue
        cbo_factor = cbo_rates.growth(component, year, base_year)
        hi_factor = hawaii_factors.get(component, 1.0)
        net_factor = net_growth_factor(cbo_factor, hi_factor)
        factor += share * net_factor
    return factor


def build_agi_targets(
    year: int,
    *,
    cbo_vintage: str = "2025-01",
    hawaii_factors: Optional[Dict[str, float]] = None,
    base_agi: Optional[Dict[Tuple[float, float], float]] = None,
    base_year: int = dotax_base.BASE_YEAR,
    include_soi_tiers: bool = True,
    soi_tier_total_filers: Optional[int] = None,
    soi_year: int = 2022,
) -> ForwardAGITargets:
    """Construct ``ForwardAGITargets`` for the given year.

    Parameters
    ----------
    year:
        Target tax year (2022-2031 supported).
    cbo_vintage:
        CBO Outlook vintage identifier (passed to ``load_cbo_rates``).
    hawaii_factors:
        Per-component HI calibration. Defaults to ``DEFAULT_HAWAII_FACTORS``.
    base_agi:
        Override the baseline AGI per bracket (mainly for testing).
    base_year:
        DOTAX edition the baseline comes from (``dotax_base``).
    include_soi_tiers:
        If True (default), build per-tier targets within $1M+ in addition
        to the aggregate $1M+ bracket target.
    soi_tier_total_filers:
        HI $1M+ filer count (default: DOTAX ``base_year``'s). Used to allocate
        national SOI tier proportions to HI.
    soi_year:
        IRS SOI Table 1.4 vintage the tier averages come from. Those are
        national TY2022 dollars, so they age from this year, not ``base_year``.
    """
    from tax_modeler.calibration.cbo_aging import (
        load_cbo_rates,
        DEFAULT_HAWAII_FACTORS,
    )

    cbo_rates = load_cbo_rates(cbo_vintage)
    hi = dict(hawaii_factors or DEFAULT_HAWAII_FACTORS)
    bagi = dict(base_agi or dotax_base.agi_targets_M(base_year))
    if soi_tier_total_filers is None:
        soi_tier_total_filers = dotax_base.filer_targets(base_year)[(1_000_000, np.inf)]

    # ── Per-bracket AGI mass ──────────────────────────────────────────────────
    bracket_targets: Dict[Tuple[float, float], float] = {}
    for bracket, base_M in bagi.items():
        comp = _BRACKET_COMPOSITIONS.get(bracket, {"wages": 1.0})
        gf = _component_growth_factor(comp, year, cbo_rates, hi, base_year)
        bracket_targets[bracket] = base_M * gf

    # ── Per-tier AGI mass within $1M+ ────────────────────────────────────────
    # Use SOI anchor tier compositions directly (same source the synthesis
    # reads) so target growth factors match synthesis-produced tier averages.
    tier_targets: Dict[Tuple[float, float], float] = {}
    if include_soi_tiers:
        from tax_modeler.calibration.soi_top_anchor import load_soi_top_anchors

        try:
            anchors = load_soi_top_anchors(year=soi_year)
            total_nat_returns = sum(a.n_returns_national for a in anchors)
            for a in anchors:
                tier_share = a.n_returns_national / total_nat_returns
                tier_filers = soi_tier_total_filers * tier_share
                tier_lo = a.agi_lo
                tier_hi = a.agi_hi if a.agi_hi is not None else np.inf

                # Tier-specific composition from the SOI anchor (matches what
                # synthesize_top_filers_from_soi uses internally).
                other_share = max(
                    0.0,
                    1.0 - (a.wages_share + a.business_share + a.capgain_share
                           + a.dividends_share + a.interest_share
                           + a.retirement_share),
                )
                comp = {
                    "wages":         a.wages_share,
                    "business":      a.business_share,
                    "capital_gains": a.capgain_share,
                    "dividends":     a.dividends_share,
                    "interest":      a.interest_share,
                    "retirement":    a.retirement_share,
                    "other":         other_share,
                }
                gf = _component_growth_factor(comp, year, cbo_rates, hi, soi_year)
                aged_tier_avg_agi = a.avg_agi * gf
                tier_targets[(tier_lo, tier_hi)] = (
                    tier_filers * aged_tier_avg_agi / 1e6
                )
        except FileNotFoundError as exc:
            logger.warning("SOI Table 1.4 not available — skipping tier targets: %s", exc)

    aggregate_agi_M = sum(bracket_targets.values())
    return ForwardAGITargets(
        year=year,
        bracket_agi_targets=bracket_targets,
        tier_agi_targets=tier_targets,
        aggregate_agi_M=aggregate_agi_M,
    )


def summarize(target: ForwardAGITargets) -> str:
    """One-line summary."""
    n_b = len(target.bracket_agi_targets)
    n_t = len(target.tier_agi_targets)
    return (
        f"ForwardAGITargets[{target.year}]: aggregate=${target.aggregate_agi_M:,.0f}M, "
        f"{n_b} brackets, {n_t} SOI tiers"
    )
