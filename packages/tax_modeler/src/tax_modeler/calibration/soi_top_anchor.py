"""SOI-Table-1.4-anchored synthesis for $1M+ filers.

Replaces the Pareto-derived ``UltraHighIncomeSynthesizerV2`` for the $1M+
bracket with a direct anchor on national IRS SOI Table 1.4 (TY 2022, Pub
1304). Three things change vs the Pareto path:

1. **Tier counts come from SOI, not Pareto.** The empirical national
   distribution across $1M-$1.5M / $1.5M-$2M / $2M-$5M / $5M-$10M / $10M+
   is fatter than a Pareto(α=1.5) tail at the high end (more $5M+ filers
   than the Pareto closed form predicts). Anchoring directly to SOI
   counts removes the closed-form approximation error.

2. **Per-tier income composition (CG/wages/business/dividends) comes from
   SOI, not from a national-95% lookup table.** Each tier's
   ``synthetic_cg_share`` is set from the SOI capgain/AGI ratio for that
   tier, with an optional Hawaii adjustment factor.

3. **Filer count is anchored to the forward DOTAX-projected $1M+ target**
   (resident-only), not to a fixed 1,824. The forward target carries the
   COR-implied growth from TY2022 → TY-of-interest.

What stays the same
-------------------
- Filing-status mix at $1M+: DOTAX A8 high-income (69% MFJ / 22% Single /
  5% HoH / 4% MFS). National SOI Table 1.4 doesn't break composition by
  filing status, so we keep the DOTAX mix.
- Honolulu county/PUMA assignment.
- Tax rescale: optional uniform income scale ``k`` to land aggregate
  Hawaii tax on the COR-implied $1M+ tax target after ``_compute_base_tax``
  reruns. Same pattern as ``calibrate_synthetic_tail_to_tax_target``.

Hawaii non-resident note
------------------------
The DOTAX A8 $1M+ filer count is resident-only, while SOI Table 1.4 is
filer-of-record (national). Composition shares (wages, CG) don't depend
on residency, so applying national shares to the DOTAX-anchored Hawaii
resident count is internally consistent — wages are wages, CG is CG.
The aggregate Hawaii tax target (from COR forecast) is also resident-only,
so the rescale step lands on the right number by construction.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tax_modeler.calibration.cbo_aging import net_growth_factor

logger = logging.getLogger(__name__)

# DOTAX A8 high-income filing status mix (matches existing synthesizer).
HIGH_INCOME_STATUS_SHARES: Dict[str, float] = {
    "married_filing_jointly":     0.69,
    "single":                     0.22,
    "head_of_household":          0.05,
    "married_filing_separately":  0.04,
}

FILING_STATUS_HAWAII_MAP: Dict[str, str] = {
    "married_filing_jointly":     "Joint_Surviving_Spouse",
    "single":                     "Single_Married_Separate",
    "married_filing_separately":  "Single_Married_Separate",
    "head_of_household":          "Head_of_Household",
}

HAWAII_STD_DEDUCTIONS: Dict[str, int] = {
    "married_filing_jointly":     4_400,
    "head_of_household":          3_212,
    "single":                     2_200,
    "married_filing_separately":  2_200,
}

HONOLULU_COUNTY = "Honolulu"
HONOLULU_PUMA = "0301"


@dataclass(frozen=True)
class SOITierAnchor:
    """Per-tier composition snapshot from national SOI Table 1.4."""
    agi_lo: float
    agi_hi: float
    n_returns_national: int
    avg_agi: float           # AGI / n_returns (per filer, $)
    wages_share: float       # wages / AGI
    capgain_share: float     # net cap gain / AGI
    business_share: float    # Sch C + partnership + S-corp / AGI
    dividends_share: float   # ordinary dividends / AGI
    interest_share: float    # taxable interest / AGI
    retirement_share: float  # IRA + pensions + SS / AGI


def _default_soi_csv_path(year: int) -> Path:
    repo_root = Path(__file__).resolve().parents[5]
    return (
        repo_root / "data" / "tax_modeler" / "irs_soi"
        / f"national_soi_table_1_4_{year}.csv"
    )


def load_soi_top_anchors(
    year: int = 2022,
    csv_path: Optional[Path] = None,
    min_lo: float = 1_000_000.0,
) -> List[SOITierAnchor]:
    """Load SOI Table 1.4 tiers with ``agi_lo >= min_lo``.

    Defaults to $1M+ tiers (5 rows: $1-$1.5M, $1.5-$2M, $2-$5M, $5-$10M,
    $10M+). Set ``min_lo=500_000`` to also include the $500K-$1M tier.
    """
    path = csv_path or _default_soi_csv_path(year)
    if not path.exists():
        raise FileNotFoundError(
            f"SOI Table 1.4 CSV not found: {path}. "
            f"Run: uv run python -m tax_modeler.scripts.parse_soi_table_14"
        )
    df = pd.read_csv(path)
    df = df[df["agi_lo"] >= min_lo].copy()
    df = df.sort_values("agi_lo").reset_index(drop=True)

    anchors: List[SOITierAnchor] = []
    for _, row in df.iterrows():
        n = float(row["n_returns"])
        agi_M = float(row["agi_less_deficit_M"])
        if agi_M <= 0 or n <= 0:
            continue
        avg_agi = (agi_M * 1e6) / n
        anchors.append(SOITierAnchor(
            agi_lo=float(row["agi_lo"]),
            agi_hi=float(row["agi_hi"]),
            n_returns_national=int(n),
            avg_agi=avg_agi,
            wages_share=float(row["wages_M"]) / agi_M,
            capgain_share=float(row["capgain_M"]) / agi_M,
            business_share=float(row["business_M"]) / agi_M,
            dividends_share=float(row["dividends_M"]) / agi_M,
            interest_share=float(row["taxable_interest_M"]) / agi_M,
            retirement_share=float(row["retirement_M"]) / agi_M,
        ))
    return anchors


def _aged_tier_avg_agi(
    anchor: SOITierAnchor,
    target_year: int,
    cbo_rates,
    hawaii_factors: Dict[str, float],
) -> float:
    """Age an SOI tier's average AGI forward via CBO components × HI factors.

    Decomposes the tier's avg AGI into seven components using SOI shares,
    applies per-component (CBO factor × HI factor) growth, and sums.
    Returns the aged per-filer AGI for ``target_year``.

    The "other" component soaks up the residual share so the
    decomposition sums to 1.0.
    """
    other_share = max(
        0.0,
        1.0 - (anchor.wages_share + anchor.business_share + anchor.capgain_share
               + anchor.dividends_share + anchor.interest_share
               + anchor.retirement_share),
    )
    components = {
        "wages":         anchor.wages_share,
        "business":      anchor.business_share,
        "capital_gains": anchor.capgain_share,
        "dividends":     anchor.dividends_share,
        "interest":      anchor.interest_share,
        "retirement":    anchor.retirement_share,
        "other":         other_share,
    }
    aged_per_dollar = 0.0
    for comp, share in components.items():
        cbo_f = cbo_rates.factor(comp, target_year)
        hi_f = hawaii_factors.get(comp, 1.0)
        aged_per_dollar += share * net_growth_factor(cbo_f, hi_f)
    return anchor.avg_agi * aged_per_dollar


def _aged_tier_capgain_share(
    anchor: SOITierAnchor,
    target_year: int,
    cbo_rates,
    hawaii_factors: Dict[str, float],
    hawaii_capgain_adjustment: float,
) -> float:
    """Recompute the tier's CG share after component aging.

    CG grows faster than wages (per CBO), so the CG share drifts up over
    the projection horizon. Returns ``aged_cg / aged_total`` for the tier,
    times the Hawaii CG adjustment.
    """
    other_share = max(
        0.0,
        1.0 - (anchor.wages_share + anchor.business_share + anchor.capgain_share
               + anchor.dividends_share + anchor.interest_share
               + anchor.retirement_share),
    )
    components = {
        "wages":         anchor.wages_share,
        "business":      anchor.business_share,
        "capital_gains": anchor.capgain_share,
        "dividends":     anchor.dividends_share,
        "interest":      anchor.interest_share,
        "retirement":    anchor.retirement_share,
        "other":         other_share,
    }
    aged_total = 0.0
    aged_cg = 0.0
    for comp, share in components.items():
        cbo_f = cbo_rates.factor(comp, target_year)
        hi_f = hawaii_factors.get(comp, 1.0)
        aged = share * net_growth_factor(cbo_f, hi_f)
        aged_total += aged
        if comp == "capital_gains":
            aged_cg = aged
    cg_share = aged_cg / aged_total if aged_total > 0 else 0.0
    return min(1.0, max(0.0, cg_share * hawaii_capgain_adjustment))


def synthesize_top_filers_from_soi(
    df: pd.DataFrame,
    *,
    target_filer_count: int,
    target_tax_M: Optional[float] = None,
    soi_anchors: Optional[List[SOITierAnchor]] = None,
    soi_year: int = 2022,
    hawaii_capgain_adjustment: float = 0.95,
    target_year: Optional[int] = None,
    cbo_vintage: Optional[str] = None,
    cbo_hawaii_factors: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """Replace existing $1M+ filers with SOI-Table-1.4-anchored synthetic rows.

    Parameters
    ----------
    df:
        Tax units DataFrame with at least ``income``/``agi``, ``filing_status``,
        ``weight``, ``hi_tax_liability``/``hi_state_tax``.
    target_filer_count:
        Forward-year DOTAX-projected $1M+ filer count (e.g., 2,703 for
        TY2027). Distributed across SOI tiers proportional to national
        tier shares.
    target_year:
        If provided alongside ``cbo_vintage``, age each tier's avg AGI
        forward via per-component CBO factors × HI calibration. Without
        this, tier AGIs are TY2022-vintage (the SOI base year) — useful
        only if the caller has separately handled top-bracket aging.
    cbo_vintage:
        CBO Outlook vintage for tier aging (e.g. ``"2025-01"``). Required
        when ``target_year`` is set.
    cbo_hawaii_factors:
        Per-component HI calibration multipliers. Defaults to
        ``cbo_aging.DEFAULT_HAWAII_FACTORS``. Only used when
        ``target_year`` and ``cbo_vintage`` are set.
    target_tax_M:
        Forward-year COR-implied $1M+ aggregate Hawaii tax ($M). If
        provided, caller should run ``calibrate_synthetic_tail_to_tax_target``
        after ``_compute_base_tax`` to land aggregate tax on this number.
        Logged here for traceability; not applied directly.
    soi_anchors:
        Pre-loaded list of ``SOITierAnchor``. If None, loaded from the
        default CSV cache for ``soi_year``.
    soi_year:
        SOI publication year to load when ``soi_anchors`` is None.
    hawaii_capgain_adjustment:
        Multiplicative factor on national CG share. Default 0.95 (DOTAX
        Table 21 indicates Hawaii residents have slightly lower CG share
        than the national average at high incomes).

    Returns
    -------
    DataFrame with synthetic $1M+ rows appended. Existing $1M+ filers
    have their weights zeroed. Caller must run ``_compute_base_tax``
    afterwards to populate ``hi_tax_liability``.
    """
    if soi_anchors is None:
        soi_anchors = load_soi_top_anchors(year=soi_year)
    if not soi_anchors:
        raise ValueError("No SOI anchors loaded — cannot synthesize")

    # Optional CBO-based tier aging: age each tier's avg AGI forward via
    # component-specific growth rates × HI calibration. Without this, tier
    # AGIs are SOI-vintage (TY2022) and downstream Phase 2 has to inflate
    # per-filer tax to reach forward COR targets. With this, the tiers are
    # already correctly sized per ITEP methodology.
    cbo_rates = None
    hi_factors = None
    if target_year is not None and cbo_vintage is not None:
        from tax_modeler.calibration.cbo_aging import (
            DEFAULT_HAWAII_FACTORS,
            load_cbo_rates,
        )
        cbo_rates = load_cbo_rates(vintage=cbo_vintage)
        hi_factors = (
            dict(cbo_hawaii_factors)
            if cbo_hawaii_factors is not None
            else dict(DEFAULT_HAWAII_FACTORS)
        )
        logger.info(
            "SOI tier aging: applying CBO %s + HI factors to %d tiers, TY%d",
            cbo_vintage, len(soi_anchors), target_year,
        )

    work = df.copy()
    if "agi" not in work.columns and "income" in work.columns:
        work["agi"] = work["income"]
    if "hi_state_tax" not in work.columns and "hi_tax_liability" in work.columns:
        work["hi_state_tax"] = work["hi_tax_liability"]
    if "num_adults" not in work.columns:
        work["num_adults"] = work["filing_status"].apply(
            lambda fs: 2 if fs == "married_filing_jointly" else 1
        )

    # Distribute target_filer_count across SOI tiers using national tier shares
    total_national = sum(a.n_returns_national for a in soi_anchors)
    tier_filer_counts: List[Tuple[SOITierAnchor, float]] = [
        (a, target_filer_count * a.n_returns_national / total_national)
        for a in soi_anchors
    ]

    # Drop existing $1M+ filer weights
    mask_1m = work["agi"] >= 1_000_000
    pre_weight = float(work.loc[mask_1m, "weight"].sum())
    pre_tax_M = float(
        (work.loc[mask_1m, "hi_state_tax"] * work.loc[mask_1m, "weight"]).sum()
    ) / 1e6
    work.loc[mask_1m, "weight"] = 0.0

    # Build synthetic rows: one per (tier × filing_status)
    synthetic_rows: List[Dict] = []
    for anchor, tier_filers in tier_filer_counts:
        # Hawaii-adjusted CG share, capped at 1.0
        if cbo_rates is not None:
            avg_agi = _aged_tier_avg_agi(anchor, target_year, cbo_rates, hi_factors)
            cg_share = _aged_tier_capgain_share(
                anchor, target_year, cbo_rates, hi_factors,
                hawaii_capgain_adjustment,
            )
        else:
            avg_agi = anchor.avg_agi
            cg_share = min(
                1.0, max(0.0, anchor.capgain_share * hawaii_capgain_adjustment)
            )

        for status, status_share in HIGH_INCOME_STATUS_SHARES.items():
            weight = tier_filers * status_share
            if weight < 0.01:
                continue
            num_adults = 2 if status == "married_filing_jointly" else 1
            num_dependents = (
                2 if status == "married_filing_jointly"
                else (1 if status == "head_of_household" else 0)
            )
            synthetic_rows.append({
                "agi": avg_agi,
                "income": avg_agi,
                "filing_status": status,
                "filing_status_hawaii": FILING_STATUS_HAWAII_MAP[status],
                "num_adults": num_adults,
                "num_dependents": num_dependents,
                "weight": weight,
                "is_synthetic_ultra_high": True,
                "synthetic_total_income": avg_agi,
                "synthetic_cg_share": cg_share,
                "synthetic_wages_share": anchor.wages_share,
                "synthetic_business_share": anchor.business_share,
                "synthetic_tier_lo": anchor.agi_lo,
                "synthetic_tier_hi": anchor.agi_hi,
                "total_deductions": 0.0,
                "standard_deduction": HAWAII_STD_DEDUCTIONS[status],
                "county": HONOLULU_COUNTY,
                "PUMA": HONOLULU_PUMA,
                # TCI ≈ AGI for high-income filers (small SS/transfer component
                # at $1M+). Without this the quintile classifier defaults TCI
                # to 0 for synthetic rows and bins them into Q1.
                "total_cash_income": avg_agi,
            })

    if not synthetic_rows:
        logger.warning("SOI top anchor produced no synthetic rows")
        return work

    synth_df = pd.DataFrame(synthetic_rows)

    # Fill remaining columns to match the parent schema
    for col in work.columns:
        if col in synth_df.columns:
            continue
        if "tax" in col.lower():
            synth_df[col] = 0.0
        elif col in ("agi_without_cap_gains", "agi_with_cap_gains"):
            synth_df[col] = synth_df["agi"]
        elif col in ("taxable_income", "hi_taxable_income", "hi_tax_taxable_income"):
            synth_df[col] = (synth_df["agi"] - synth_df["standard_deduction"]).clip(lower=0)
        elif col in ("has_capital_gains", "capital_gains", "agi_adjustments"):
            synth_df[col] = 0.0
        elif col == "bracket":
            synth_df[col] = "1000000+"
        elif col in ("filer_id", "primary_filer_id", "SERIALNO", "hh_id"):
            synth_df[col] = [f"SOI_SYNTH_{i}" for i in range(len(synth_df))]
        elif col == "secondary_filer_id":
            synth_df[col] = [
                f"SOI_SYNTH_{i}_SPOUSE" if r["filing_status"] == "married_filing_jointly" else None
                for i, r in synth_df.iterrows()
            ]
        elif col == "dependents":
            synth_df[col] = [[] for _ in range(len(synth_df))]
        elif col in ("hh_weight", "person_weight_sum", "weight_original", "weight_calibrated"):
            synth_df[col] = synth_df["weight"]
        elif col == "calibration_factor":
            synth_df[col] = 1.0
        elif col in ("primary_wagp", "earned_income"):
            synth_df[col] = synth_df["agi"] * (1 - synth_df["synthetic_cg_share"])
        elif col in ("primary_intp", "investment_income"):
            synth_df[col] = synth_df["agi"] * synth_df["synthetic_cg_share"]
        else:
            synth_df[col] = 0.0

    result = pd.concat([work, synth_df], ignore_index=True)

    # Logging
    post_weight = float(result.loc[result["agi"] >= 1_000_000, "weight"].sum())
    avg_cg = float(
        (synth_df["synthetic_cg_share"] * synth_df["weight"]).sum()
        / synth_df["weight"].sum()
    )
    logger.info("=" * 72)
    logger.info("SOI-anchored $1M+ synthesis (Table 1.4 TY%d, HI CG adj=%.2f)",
                soi_year, hawaii_capgain_adjustment)
    logger.info("  pre  $1M+: weight=%.0f  tax=$%.1fM", pre_weight, pre_tax_M)
    logger.info("  post $1M+: weight=%.0f (target %d)", post_weight, target_filer_count)
    logger.info("  weighted-avg CG share: %.1f%%", avg_cg * 100)
    if target_tax_M is not None:
        logger.info("  target tax (rescale to apply): $%.1fM", target_tax_M)
    for anchor, tier_filers in tier_filer_counts:
        label = (
            f"${anchor.agi_lo/1e6:.1f}M-${anchor.agi_hi/1e6:.1f}M"
            if anchor.agi_hi != float("inf")
            else f"${anchor.agi_lo/1e6:.0f}M+"
        )
        cg_share = min(1.0, anchor.capgain_share * hawaii_capgain_adjustment)
        logger.info("  tier %-14s: %5.0f filers  avg AGI $%.2fM  CG=%.1f%%  wages=%.1f%%",
                    label, tier_filers, anchor.avg_agi / 1e6,
                    cg_share * 100, anchor.wages_share * 100)
    logger.info("=" * 72)
    return result
