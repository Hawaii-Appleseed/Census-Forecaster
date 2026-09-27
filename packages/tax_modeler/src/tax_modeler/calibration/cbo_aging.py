"""CBO Outlook component growth rates for ITEP-style filer-level aging.

Loads per-component nominal annual growth factors from a versioned CSV
cache (``data/tax_modeler/cbo/cbo_components_<vintage>.csv``) and
provides per-filer aging that grows each income component at its own
CBO rate, optionally with Hawaii calibration factors.

This is the dynamic counterpart to ``soi_top_anchor`` (static composition):
SOI fixes the *shape* of the income mix per tier; CBO aging fixes the
*growth* of each component within that mix. Together they replicate
ITEP's methodology: anchor on administrative composition, age each
component on CBO macro projections.

Usage
-----
::

    from tax_modeler.calibration.cbo_aging import (
        load_cbo_rates, age_filers_with_components,
    )

    rates = load_cbo_rates(vintage="2025-01")
    aged = age_filers_with_components(
        units, target_year=2027, base_year=2022,
        cbo_rates=rates,
        hawaii_factors={"wages": 0.85, "capital_gains": 1.00, ...},
    )

The ``soft_anchor_to_cor`` knob lets the aged result land near (but not
exactly on) a COR aggregate target — useful when CBO and COR disagree
about Hawaii's macro outlook. Default ±3% adjustment cap; failures
above that signal model drift and should be investigated, not silently
absorbed.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Components published per CBO and supported by the aging engine.
CBO_COMPONENTS: tuple[str, ...] = (
    "wages",
    "proprietors",
    "business",        # alias of proprietors for compatibility
    "capital_gains",
    "dividends",
    "interest",
    "retirement",
    "other",
)

# Default Hawaii calibration factors (empirical, derived TY2014-TY2022).
# - wages 0.85: HI wage CAGR ~85% of national CBO (BEA SAINC4)
# - business/proprietors 0.90: tourism-driven, slower growth
# - capital_gains 0.85: HI net LTCG CAGR 9.6%/yr vs national 10.5%/yr
#   (DOTAX TY2014-TY2022); HI wealth is real-estate-heavy (lower CG turnover);
#   COR Jan 2026 forecast implies CG ~3-5%/yr through FY2032 (well below CBO).
#   Confidence: medium (10 datapoints, volatile series, no published HI/S&P
#   elasticity). See research notes 2026-05-05.
# - dividends/interest 1.00: national financial markets, federal-rate-driven
# - retirement 1.00: SS / pensions federally indexed
# - other 0.90: mostly rental; HI housing market cooler post-2022
DEFAULT_HAWAII_FACTORS: Dict[str, float] = {
    "wages":         0.85,
    "proprietors":   0.90,
    "business":      0.90,
    "capital_gains": 0.85,
    "dividends":     1.00,
    "interest":      1.00,
    "retirement":    1.00,
    "other":         0.90,
}


def net_growth_factor(cbo_f: float, hi_f: float) -> float:
    """Combine a CBO national growth factor with its Hawaii calibration ratio.

    The Hawaii factor is a CAGR *ratio* (e.g. wages 0.85 = "HI wage CAGR ~85%
    of national", per ``DEFAULT_HAWAII_FACTORS``), so it scales the growth
    *increment*, not the level::

        net = 1 + hi_f * (cbo_f - 1)

    Applying it as a level multiplier (``cbo_f * hi_f``) is a units error — it
    misreads a rate ratio as a level ratio and can imply Hawaii income shrinks
    while the nation grows (e.g. ``0.85 * 1.149 = 0.976 < 1``). Centralized so
    the filer-aging engine, the SOI top anchor, and the forward AGI targets
    stay on one convention; mixing them makes the stage-2 IPF rake fight
    itself.
    """
    return 1.0 + hi_f * (cbo_f - 1.0)


@dataclass(frozen=True)
class CBOComponentRates:
    """CBO per-component nominal growth factors keyed by (component, year).

    Attributes
    ----------
    vintage:
        Outlook publication identifier (e.g. ``"2025-01"``).
    base_year:
        Calendar year for which growth factors equal 1.0 (default 2022).
    factors:
        ``{component: {year: growth_factor_from_base_year}}``.
    """
    vintage: str
    base_year: int
    factors: Dict[str, Dict[int, float]]

    def factor(self, component: str, year: int) -> float:
        """Return growth factor for ``component`` at ``year``.

        Falls back to "other" if component not found, with a warning.
        Falls back to nearest-available year if year not found (extrapolating
        at the most recent observed CAGR is the caller's responsibility).
        """
        if component not in self.factors:
            logger.warning(
                "CBO factor lookup: unknown component %r, falling back to 'other'",
                component,
            )
            component = "other"
        years = self.factors[component]
        if year in years:
            return years[year]
        # Year not in table — pick nearest available
        nearest = min(years.keys(), key=lambda y: abs(y - year))
        logger.warning(
            "CBO factor lookup: year %d not in vintage %s, using nearest year %d",
            year, self.vintage, nearest,
        )
        return years[nearest]


def _default_csv_path(vintage: str) -> Path:
    repo_root = Path(__file__).resolve().parents[5]
    return repo_root / "data" / "tax_modeler" / "cbo" / f"cbo_components_{vintage}.csv"


def load_cbo_rates(
    vintage: str = "2025-01",
    csv_path: Optional[Path] = None,
    base_year: int = 2022,
) -> CBOComponentRates:
    """Load CBO component growth factors from a versioned CSV.

    Parameters
    ----------
    vintage:
        Outlook publication identifier. Looks up
        ``data/tax_modeler/cbo/cbo_components_<vintage>.csv``.
    csv_path:
        Override path; otherwise resolved from ``vintage``.
    base_year:
        Calendar year that should have growth factor 1.0. Used to validate
        the CSV.
    """
    path = csv_path or _default_csv_path(vintage)
    if not path.exists():
        raise FileNotFoundError(
            f"CBO rates CSV not found: {path}. "
            f"See data/tax_modeler/cbo/README.md for refresh instructions."
        )
    df = pd.read_csv(path)
    required = {"component", "year", "growth_factor_from_2022"}
    if not required.issubset(df.columns):
        raise ValueError(
            f"CBO CSV missing required columns: "
            f"{required - set(df.columns)}"
        )

    factors: Dict[str, Dict[int, float]] = {}
    for component, group in df.groupby("component"):
        factors[component] = dict(zip(
            group["year"].astype(int),
            group["growth_factor_from_2022"].astype(float),
        ))
        # Sanity: base_year factor should be 1.0
        if base_year in factors[component]:
            base_f = factors[component][base_year]
            if abs(base_f - 1.0) > 1e-6:
                logger.warning(
                    "CBO %s: %s base_year=%d factor=%.6f != 1.0",
                    vintage, component, base_year, base_f,
                )
    logger.info(
        "Loaded CBO %s: %d components, year range %d-%d",
        vintage, len(factors),
        df["year"].min(), df["year"].max(),
    )
    return CBOComponentRates(vintage=vintage, base_year=base_year, factors=factors)


# ─────────────────────────────────────────────────────────────────────────────
# Per-filer component aging
# ─────────────────────────────────────────────────────────────────────────────

def _component_amounts_for_filer(
    df: pd.DataFrame,
) -> Dict[str, pd.Series]:
    """Return per-component income amounts ($) for each row.

    Uses PUMS-actual decomposition columns when present
    (``primary_wagp`` etc.) and falls back to share columns
    (``synthetic_cg_share`` × ``income``) for synthesized rows. Total
    across components should equal ``income`` (or close to it — small
    drift is acceptable, the residual is bucketed into ``other``).
    """
    inc = df["income"].astype(float).to_numpy()
    n = len(df)

    def _col(name: str) -> np.ndarray:
        if name in df.columns:
            return df[name].fillna(0).astype(float).to_numpy()
        return np.zeros(n)

    # Capital gains: prefer explicit column, else synthetic_cg_share * income
    cg = inc * _col("synthetic_cg_share")

    # Wages: PUMS-actual primary+secondary, with synthetic_wages_share fallback
    # for synthetic rows where primary_wagp wasn't decomposed.
    wages_pums = _col("primary_wagp") + _col("secondary_wagp")
    wages_synth = inc * _col("synthetic_wages_share")
    # Use synth share where pums is zero AND synth share is set
    use_synth_w = (wages_pums == 0) & (_col("synthetic_wages_share") > 0)
    wages = np.where(use_synth_w, wages_synth, wages_pums)

    # Business: primary_semp + secondary_semp, with synthetic_business_share fallback
    biz_pums = _col("primary_semp") + _col("secondary_semp")
    biz_synth = inc * _col("synthetic_business_share")
    use_synth_b = (biz_pums == 0) & (_col("synthetic_business_share") > 0)
    business = np.where(use_synth_b, biz_synth, biz_pums)

    # Investment income components (PUMS-actual)
    interest = _col("primary_intp") + _col("secondary_intp")
    dividends = _col("primary_div") + _col("secondary_div")

    # Retirement (PUMS-actual; ssp_full preferred over ssp/0.85)
    ssp = _col("primary_ssp_full") + _col("secondary_ssp_full")
    if (ssp == 0).all():
        ssp = (_col("primary_ssp") + _col("secondary_ssp")) / 0.85
    retirement = (
        _col("primary_retp") + _col("secondary_retp")
        + ssp
        + _col("primary_ssip") + _col("secondary_ssip")
        + _col("primary_pap") + _col("secondary_pap")
    )

    # Other = residual to make components sum to income
    accounted = wages + business + cg + interest + dividends + retirement
    other = np.maximum(inc - accounted, 0)

    return {
        "wages": pd.Series(wages, index=df.index),
        "business": pd.Series(business, index=df.index),
        "capital_gains": pd.Series(cg, index=df.index),
        "dividends": pd.Series(dividends, index=df.index),
        "interest": pd.Series(interest, index=df.index),
        "retirement": pd.Series(retirement, index=df.index),
        "other": pd.Series(other, index=df.index),
    }


def age_filers_with_components(
    df: pd.DataFrame,
    target_year: int,
    *,
    base_year: int = 2022,
    cbo_rates: Optional[CBOComponentRates] = None,
    hawaii_factors: Optional[Dict[str, float]] = None,
    soft_anchor_to_cor: bool = False,
    cor_year_target_M: Optional[float] = None,
    cor_anchor_tolerance: float = 0.03,
) -> pd.DataFrame:
    """Age each filer's income components separately, then re-tot to ``income``.

    For each filer, decomposes ``income`` into seven CBO components via
    PUMS-actual columns where present (``primary_wagp`` etc.) plus
    synthetic share columns (``synthetic_cg_share``, ``synthetic_wages_share``,
    ``synthetic_business_share``). Each component is grown by the CBO factor
    for ``target_year`` × the Hawaii calibration factor, and the new
    ``income`` is the sum.

    Parameters
    ----------
    df:
        Tax-units DataFrame post-calibration, pre-projection. Must have
        ``income`` and ``weight``.
    target_year:
        Calendar year to age to.
    base_year:
        The dollar-year of the *input* incomes (i.e. the PUMS income
        vintage — 2024 for the 2020-2024 5-year file). Aging runs from this
        year to ``target_year``; the cumulative-from-CSV-base CBO factors
        are re-based to it (``factor(target)/factor(base_year)``). Defaults
        to 2022 for backward compatibility, but production callers should
        pass the detected income dollar-year (see
        ``loaders.pums_loader.PUMS_INCOME_DOLLAR_YEAR`` /
        ``detect_pums_dollar_year``).
    cbo_rates:
        Pre-loaded ``CBOComponentRates``. Loaded from default vintage if
        None.
    hawaii_factors:
        Per-component multipliers for Hawaii vs national. Default uses
        ``DEFAULT_HAWAII_FACTORS``.
    soft_anchor_to_cor:
        If True, after aging, scale all incomes uniformly so aggregate
        Hawaii tax (computed downstream) lands within ``cor_anchor_tolerance``
        of ``cor_year_target_M``. Implemented by emitting a ``cbo_aging_scale``
        column the caller applies post-tax-recompute. Disabled by default
        (pure CBO aging).
    cor_year_target_M:
        Required if ``soft_anchor_to_cor=True``. The COR aggregate IIT
        target for ``target_year`` ($M).
    cor_anchor_tolerance:
        Maximum |k - 1| allowed when soft-anchoring. Beyond this,
        log loudly — signals CBO/COR macro disagreement that shouldn't
        be silently absorbed.

    Returns
    -------
    DataFrame with updated ``income`` and ``agi`` columns, plus
    ``cbo_aged_<component>`` per-component aged amounts (kept for
    diagnostics and downstream tax decomposition).
    """
    if cbo_rates is None:
        cbo_rates = load_cbo_rates()
    if hawaii_factors is None:
        hawaii_factors = dict(DEFAULT_HAWAII_FACTORS)

    out = df.copy()
    components = _component_amounts_for_filer(out)

    # Re-base the cumulative CBO factors to the *income dollar-year*.
    # ``cbo_rates.factor(comp, year)`` is cumulative growth from the CSV's
    # base_year (2022). The input incomes are already in ``base_year`` dollars
    # (e.g. the 2020-2024 5-year PUMS is in 2024 dollars), so applying the raw
    # cumulative-from-2022 factor double-counts the 2022→base_year growth. The
    # correct aging factor over the window is factor(target)/factor(base_year).
    # When base_year == cbo_rates.base_year this is a no-op (the prior behavior).
    rebase = base_year != cbo_rates.base_year

    # Aged total per component
    aged_total = pd.Series(0.0, index=out.index)
    for comp, amt in components.items():
        cbo_f = cbo_rates.factor(comp, target_year)
        if rebase:
            base_f = cbo_rates.factor(comp, base_year)
            if base_f > 0:
                cbo_f = cbo_f / base_f
        hi_f = hawaii_factors.get(comp, 1.0)
        net_f = net_growth_factor(cbo_f, hi_f)
        aged_amt = amt * net_f
        out[f"cbo_aged_{comp}"] = aged_amt
        aged_total = aged_total + aged_amt

    # Pre/post AGI summary
    pre_total_M = float((out["income"] * out["weight"]).sum() / 1e6)
    post_total_M = float((aged_total * out["weight"]).sum() / 1e6)
    growth_pct = (post_total_M / pre_total_M - 1) * 100 if pre_total_M > 0 else 0.0

    out["income"] = aged_total
    if "agi" in out.columns:
        out["agi"] = aged_total

    # Refresh synthetic_cg_share to reflect post-aging composition.
    # Component-aware aging grows CG and wages at different rates, so
    # the pre-aging share is now stale. The §235-51(f) 7.25% cap depends
    # on this share — calculate_hawaii_tax reads it directly.
    if "cbo_aged_capital_gains" in out.columns:
        cg_aged = out["cbo_aged_capital_gains"].to_numpy(dtype=float)
        income_aged = aged_total.to_numpy(dtype=float)
        new_share = np.where(income_aged > 0, cg_aged / income_aged, 0.0)
        if "synthetic_cg_share" in out.columns:
            # Update only rows that originally had a share or have aged CG;
            # leave NaN-share rows alone so non-CG-bearing filers stay 0.
            mask = out["synthetic_cg_share"].notna() | (cg_aged > 0)
            out.loc[mask, "synthetic_cg_share"] = new_share[mask]
        else:
            out["synthetic_cg_share"] = new_share

    # Soft anchor to COR
    if soft_anchor_to_cor:
        if cor_year_target_M is None:
            raise ValueError(
                "soft_anchor_to_cor=True requires cor_year_target_M"
            )
        # Caller applies the tax-aware multiplier after _compute_base_tax
        # since aggregate tax depends on bracket structure, not just AGI.
        # Here we record the intended target; year_recalibrator's Phase 2
        # already applies a per-bracket multiplier that lands aggregate tax
        # on the COR target. Just emit an info log.
        logger.info(
            "CBO aging soft-anchor: COR target $%.0fM for TY%d "
            "(applied via Phase 2 per-bracket multiplier downstream)",
            cor_year_target_M, target_year,
        )

    logger.info(
        "CBO aging (vintage %s, TY%d): aggregate AGI $%.0fM → $%.0fM (%.1f%%)",
        cbo_rates.vintage, target_year, pre_total_M, post_total_M, growth_pct,
    )
    # Log per-component growth contribution for the top-bracket population
    top = out["income"] >= 1_000_000
    if top.any():
        for comp in CBO_COMPONENTS:
            if comp == "proprietors":
                continue  # alias of business
            col = f"cbo_aged_{comp}"
            if col in out.columns:
                top_M = float((out.loc[top, col] * out.loc[top, "weight"]).sum() / 1e6)
                if top_M > 1.0:
                    cbo_f = cbo_rates.factor(comp, target_year)
                    if rebase:
                        base_f = cbo_rates.factor(comp, base_year)
                        if base_f > 0:
                            cbo_f = cbo_f / base_f
                    hi_f = hawaii_factors.get(comp, 1.0)
                    logger.info(
                        "  $1M+ %-13s: $%6.0fM aged (factor=%.3f × HI %.2f = %.3f)",
                        comp, top_M, cbo_f, hi_f, net_growth_factor(cbo_f, hi_f),
                    )

    return out
