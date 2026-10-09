"""Non-resident filer uplift for resident-only Hawaii microsim.

Our microsim is anchored on PUMS Hawaii residents and DOTAX resident-filer
statistics, so it captures resident liability only. Non-residents (Form
N-15) and composite returns (S-corp / partnership pass-throughs) add a
material layer of HI-source income tax that ITEP-style fiscal scoring
includes by default.

Source: DOTAX *Hawaiʻi Individual Income Tax Statistics, Tax Year 2023*
(https://files.hawaii.gov/tax/stats/stats/indinc/2023indinc.pdf; parsed by
``scripts/parse_dotax_indinc.py`` into ``data/calibration/dotax_indinc_2023.json``)

TY2023 anchor figures (before credits):
  - 110,261 N-15 returns (TY2022: 107,992)
  - $283M total NR liability                  (vs $2,960M resident → 9.6%)
  - $177M from ≥$400K worldwide AGI           (62.5% of NR total; Table 17A)
  - 1,170 taxable NR returns at ≥$400K HI AGI, plus 1,421 taxable composite
    returns (A-3 does not split composites by class)
  - 45% of NR taxable income at ≥$400K HI AGI is LTCG (vs 23.7% for residents;
    Table 21)
  - NR liability CAGR 2012-2023: 9.38%/yr (vs 5.4% for residents)

Not re-derived: ``DEFAULT_BRACKET_UPLIFT`` was derived on the TY2022 edition's
Hawaiʻi-AGI table of NR liability. The TY2023 edition prints NR liability by
worldwide AGI (Table 17A) and A-3 prints none, so the bases differ. This module
has no callers; re-derive before using it.

Statutory facts:
  - HRS §235-51 imposes the same rate schedule on residents and non-residents
  - §235-51(f) 7.25% LTCG alt-tax IS available to non-residents
  - SB 3125 CD1's 13% top bracket applies to NR HI-source income above $1M
    (no statutory carve-out)
  - Composite returns pay at the highest marginal rate by statute → captured
    automatically at 13% under SB 3125 CD1

Caveats:
  - DOTAX Table A-3 stops at ≥$400K HI AGI; finer NR granularity at $1M+
    not published.  Allocation between $400K-$1M and $1M+ uses the same
    proportion as the resident population (proxy).
  - 34.4% of N-15 returns are non-taxable; uplift applies to taxable returns
    only (exclude from positive-liability scaling).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


# DOTAX TY2023 anchor (before credits, $M)
NR_TOTAL_LIABILITY_2023_M: float = 283.0
NR_TOP_BRACKET_LIABILITY_2023_M: float = 177.0   # NR ≥$400K worldwide AGI (Table 17A)

# Resident TY2023 reference (DOTAX, before credits)
RES_TOTAL_LIABILITY_2023_M: float = 2_960.0

# CAGRs from DOTAX time series (2012-2023, as the report states them)
NR_GROWTH_RATE_PCT: float = 0.0938
RES_GROWTH_RATE_PCT: float = 0.054

# Bracket-weighted uplift factors derived from DOTAX Table A-3.
# Multiply resident liability per bracket by (1 + uplift) to add NR layer.
#   below_350K : low NR exposure (mostly mainland income, doesn't hit HI brackets)
#   350K_to_1M : moderate NR uplift; NR composite returns dominant
#   1M_plus    : NR uplift via large landlords + high-LTCG nonresidents
DEFAULT_BRACKET_UPLIFT: Dict[str, float] = {
    "below_350K": 0.030,
    "350K_to_1M": 0.130,
    "1M_plus":    0.100,
}


@dataclass(frozen=True)
class NRUpliftConfig:
    """Configuration for NR uplift application."""

    bracket_uplift: Dict[str, float]
    growth_rate_pct: float = NR_GROWTH_RATE_PCT
    base_year: int = 2023


DEFAULT_CONFIG = NRUpliftConfig(bracket_uplift=DEFAULT_BRACKET_UPLIFT)


def estimate_nr_total_liability_M(
    year: int, *, config: NRUpliftConfig = DEFAULT_CONFIG,
) -> float:
    """Total NR tax liability for ``year`` (before credits, $M).

    Anchors at TY2023 ($283M) and grows at ``config.growth_rate_pct``/yr.
    """
    growth = (1 + config.growth_rate_pct) ** (year - config.base_year)
    return NR_TOTAL_LIABILITY_2023_M * growth


def bracket_for_agi(agi: float) -> str:
    """Return the bracket key for an AGI value."""
    if agi < 350_000:
        return "below_350K"
    if agi < 1_000_000:
        return "350K_to_1M"
    return "1M_plus"


def apply_uplift_to_bracket_delta(
    resident_delta_by_bracket_M: Dict[str, float],
    *,
    config: NRUpliftConfig = DEFAULT_CONFIG,
) -> Dict[str, float]:
    """Add NR layer to resident-only bracket deltas.

    Parameters
    ----------
    resident_delta_by_bracket_M:
        Mapping like ``{"below_350K": 50.0, "350K_to_1M": 30.0, "1M_plus": 100.0}``
        — the resident-only revenue delta in $M for each bracket.

    Returns
    -------
    Same dict shape with NR uplift added to each bracket.
    """
    uplift = config.bracket_uplift
    return {k: v * (1 + uplift[k]) for k, v in resident_delta_by_bracket_M.items()}


def total_with_nr_uplift_M(
    resident_delta_by_bracket_M: Dict[str, float],
    *,
    config: NRUpliftConfig = DEFAULT_CONFIG,
) -> float:
    """Sum of resident + NR delta across all brackets ($M)."""
    return sum(apply_uplift_to_bracket_delta(
        resident_delta_by_bracket_M, config=config,
    ).values())
