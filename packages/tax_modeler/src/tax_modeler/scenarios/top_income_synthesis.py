"""Top-income (1M+) synthesis wrapper for the SB3125 CD1 fiscal-impact pipeline.

PUMS samples ultra-high-income filers very thinly: the calibrated tax units
recover only ~19% of the DOTAX $1M+ filer count (342 weighted vs the 1,824
target from DOTAX TY2023 data, IRS SOI 2022 Table A8). The IPF rake's
1.5x weight-cap prevents it from closing this gap on its own (a 5x
adjustment would be needed).

This module wraps :class:`UltraHighIncomeSynthesizerV2` to:
  1. Add Pareto-distributed synthetic $1M+ filers across realistic
     filing-status mix (MFJ 69% / Single 22% / HoH 5% / MFS 4% per
     DOTAX Table A8 $400K+ data).
  2. Use TOTAL income (incl. capital gains) as ``agi`` rather than
     ordinary-only, since Hawaii taxes CG at ordinary rates and the
     rest of this pipeline treats ``agi`` as total AGI.
  3. Assign synthetic rows to Honolulu county/PUMA (where ultra-high
     earners are concentrated) so projection-stage county growth
     factors apply correctly.
  4. Map synthetic columns to match the pipeline's tax-unit schema
     so downstream stages (tax recompute, projection, RevenueEstimator)
     consume them transparently.

The synthesizer should be inserted AFTER IPF calibration but BEFORE
projection, so the rake doesn't see (and try to undo) the synthetic
rows. After synthesis, the base tax must be re-computed so the
synthetic rows have proper ``hi_tax_liability`` values.

DOTAX target source: "Tax Credits Claimed by Hawaiʻi Taxpayers — Tax
Year 2023" combined with IRS SOI 2022 Hawaii Table A8 (1,824 filers
generating $663M Hawaii state tax above $1M AGI).
"""
from __future__ import annotations

import logging
from typing import Dict

import pandas as pd

logger = logging.getLogger(__name__)

# DOTAX/IRS SOI 2022 Hawaii target for $1M+ AGI bracket
DOTAX_1M_PLUS_FILER_TARGET = 1_824
DOTAX_1M_PLUS_TAX_TARGET_M = 663.0   # $ millions
PARETO_ALPHA = 1.5                    # IRS SOI 2022 tail shape

# DOTAX Table A8 targets for $500K-$1M (PUMS topcode-compression range)
DOTAX_500K_750K_FILER_TARGET = 2_549
DOTAX_750K_1M_FILER_TARGET   = 1_004
DOTAX_500K_1M_TAX_TARGET_M   = 234.0   # $149M ($500K-$750K) + $85M ($750K-$1M)

HONOLULU_COUNTY = "Honolulu"
HONOLULU_PUMA = "0301"               # urban Honolulu PUMA — high-income concentration


def synthesize_top_filers(
    df: pd.DataFrame,
    target_tax_m: float = DOTAX_1M_PLUS_TAX_TARGET_M,
    pareto_alpha: float = PARETO_ALPHA,
    total_1m_filers: int = DOTAX_1M_PLUS_FILER_TARGET,
) -> pd.DataFrame:
    """Add Pareto-derived synthetic $1M+ filers to a calibrated tax-unit DataFrame.

    Wraps :class:`UltraHighIncomeSynthesizerV2` with three pipeline-specific
    fixes (see module docstring).

    Parameters
    ----------
    df : DataFrame
        Calibrated base-year tax units. Must have columns
        ``income``, ``filing_status``, ``weight``, ``hi_tax_liability``,
        plus the standard tax-unit schema.
    target_tax_m : float
        DOTAX $1M+ bracket tax target in $ millions. Defaults to $663M
        (IRS SOI 2022 Hawaii).
    pareto_alpha : float
        Shape parameter for the Pareto income distribution above $1M.
    total_1m_filers : int
        Total target weighted-filer count for the $1M+ bracket.

    Returns
    -------
    DataFrame
        Original df with synthetic $1M+ rows appended (and existing
        $1M+ filers' weights zeroed). Caller should run base-tax
        recomputation to populate ``hi_tax_liability`` etc. on the
        synthetic rows.
    """
    from tax_modeler.adjustments.ultra_high_income_synthesizer_v2 import (
        UltraHighIncomeSynthesizerV2,
    )

    # V2 expects 'agi' and 'hi_state_tax'; pipeline uses 'income' and
    # 'hi_tax_liability'. Ensure the aliases V2 needs are present.
    work = df.copy()
    if "agi" not in work.columns and "income" in work.columns:
        work["agi"] = work["income"]
    if "hi_state_tax" not in work.columns and "hi_tax_liability" in work.columns:
        work["hi_state_tax"] = work["hi_tax_liability"]
    if "num_adults" not in work.columns:
        # Best-effort: MFJ = 2 adults, others = 1
        work["num_adults"] = work["filing_status"].apply(
            lambda fs: 2 if fs == "married_filing_jointly" else 1
        )

    synth = UltraHighIncomeSynthesizerV2(
        pareto_alpha=pareto_alpha, total_1m_filers=total_1m_filers
    )
    result = synth.calibrate(work, target_tax_m=target_tax_m)

    # Identify synthetic rows
    synth_mask = result.get(
        "is_synthetic_ultra_high", pd.Series(False, index=result.index)
    ).fillna(False).astype(bool)

    if synth_mask.sum() == 0:
        logger.warning("Synthesizer produced no synthetic rows")
        return result

    # ---- Override 1: use TOTAL income as AGI ---------------------------
    # V2 sets agi = ordinary_agi (excludes capital gains). For Hawaii
    # tax purposes, CG is taxed at ordinary rates, so the AGI used by
    # downstream tax calc should include CG.
    if "synthetic_total_income" in result.columns:
        synth_total = result.loc[synth_mask, "synthetic_total_income"]
        result.loc[synth_mask, "agi"] = synth_total
        if "income" in result.columns:
            result.loc[synth_mask, "income"] = synth_total

    # ---- Override 2: assign Honolulu geography -------------------------
    if "county" in result.columns:
        result.loc[synth_mask, "county"] = HONOLULU_COUNTY
    if "PUMA" in result.columns:
        result.loc[synth_mask, "PUMA"] = HONOLULU_PUMA

    # ---- Override 3: map to pipeline's earned/investment income ---------
    # Set primary_wagp + primary_intp consistent with synthetic CG share,
    # so income decomposition is internally consistent if used elsewhere.
    if "synthetic_cg_share" in result.columns and "synthetic_total_income" in result.columns:
        cg_share = result.loc[synth_mask, "synthetic_cg_share"]
        total = result.loc[synth_mask, "synthetic_total_income"]
        for col in ("primary_wagp", "earned_income"):
            if col in result.columns:
                result.loc[synth_mask, col] = (total * (1 - cg_share)).astype(float)
        for col in ("primary_intp", "investment_income"):
            if col in result.columns:
                result.loc[synth_mask, col] = (total * cg_share).astype(float)

    # ---- Override 4: weight columns aligned ----------------------------
    if "hh_weight" in result.columns:
        result.loc[synth_mask, "hh_weight"] = result.loc[synth_mask, "weight"]

    # ---- Re-zero hi_state_tax / hi_tax_liability so caller recomputes ---
    for col in ("hi_state_tax", "hi_tax_liability", "hi_agi", "hi_taxable_income",
                "hi_tax_before_credits", "hi_low_income_credit",
                "ctc_total", "ctc_refundable", "eitc_amount"):
        if col in result.columns:
            result.loc[synth_mask, col] = 0.0

    logger.info(
        "Top-income synthesis: added %d synthetic rows, total weight %.0f, "
        "income range $%.1fM–$%.1fM",
        int(synth_mask.sum()),
        float(result.loc[synth_mask, "weight"].sum()),
        float(result.loc[synth_mask, "agi"].min() / 1e6),
        float(result.loc[synth_mask, "agi"].max() / 1e6),
    )
    return result


def validate_top_synthesis(df: pd.DataFrame) -> Dict[str, float]:
    """Sanity-check $1M+ coverage after synthesis.

    Returns a dict of diagnostics:
      - filers_1m_plus:        weighted filer count >= $1M
      - tax_1m_plus_M:         weighted Hawaii tax for >= $1M ($M)
      - filer_target_ratio:    actual / DOTAX_1M_PLUS_FILER_TARGET
      - tax_target_ratio:      actual / DOTAX_1M_PLUS_TAX_TARGET_M
      - synthetic_count:       weighted synthetic-row count
      - mfj_share / single_share / hoh_share / mfs_share: filing-status shares of $1M+ filers
    """
    income_col = "agi" if "agi" in df.columns else "income"
    tax_col = "hi_tax_liability" if "hi_tax_liability" in df.columns else "hi_state_tax"

    mask_1m = df[income_col] >= 1_000_000
    filers_1m = float(df.loc[mask_1m, "weight"].sum())
    tax_1m_M = float((df.loc[mask_1m, tax_col] * df.loc[mask_1m, "weight"]).sum()) / 1e6

    if "is_synthetic_ultra_high" in df.columns:
        synth_mask = df["is_synthetic_ultra_high"].fillna(False).astype(bool)
        synth_w = float(df.loc[synth_mask, "weight"].sum())
    else:
        synth_w = 0.0

    out = {
        "filers_1m_plus":      filers_1m,
        "tax_1m_plus_$M":      tax_1m_M,
        "filer_target_ratio":  filers_1m / DOTAX_1M_PLUS_FILER_TARGET,
        "tax_target_ratio":    tax_1m_M / DOTAX_1M_PLUS_TAX_TARGET_M,
        "synthetic_weight":    synth_w,
    }
    if filers_1m > 0:
        for fs_label, fs_keys in [
            ("mfj_share",    ["married_filing_jointly"]),
            ("single_share", ["single"]),
            ("hoh_share",    ["head_of_household"]),
            ("mfs_share",    ["married_filing_separately"]),
        ]:
            mask = mask_1m & df["filing_status"].isin(fs_keys)
            out[fs_label] = float(df.loc[mask, "weight"].sum()) / filers_1m
    return out


def rescale_synthetic_tail_to_tax_target(
    df: pd.DataFrame,
    target_tax_m: float = DOTAX_1M_PLUS_TAX_TARGET_M,
) -> tuple:
    """Scale synthesized $1M+ filer incomes uniformly so aggregate Hawaii tax
    on the synthetic tail matches ``target_tax_m``.

    This closes the gap between Pareto-distribution-derived income levels and
    the DOTAX/IRS SOI tax benchmark.  The Pareto synthesizer hits the filer
    count target exactly but typically recovers only ~88% of the $663M tax
    target because the conditional-mean income per tier underestimates the
    very top of the tail.  A uniform income scale factor ``k`` applied to all
    synthetic filer incomes corrects this.

    At $1M+ income Hawaii's effective marginal rate is approximately flat at
    11% (top bracket), so ``tax ≈ k × income × rate`` — a single-pass k
    lands within ~0.5% of target after ``_compute_base_tax()`` reruns. No
    iteration is required.

    Must be called AFTER ``synthesize_top_filers()`` AND ``_compute_base_tax()``.
    Clears stale tax columns on synthetic rows; caller must re-run
    ``_compute_base_tax()`` after this call.

    Parameters
    ----------
    df : DataFrame
        Tax units after synthesis and base-tax computation.
    target_tax_m : float
        Target aggregate Hawaii tax for $1M+ synthetic filers, in $ millions.
        Defaults to ``DOTAX_1M_PLUS_TAX_TARGET_M`` ($663M).

    Returns
    -------
    tuple[DataFrame, float]
        ``(rescaled_df, k)`` where ``k`` is the uniform scale factor applied
        to all income-related columns on synthetic rows.  ``k > 1`` means
        incomes were scaled up to reach the tax target.
    """
    mask = df["is_synthetic_ultra_high"].fillna(False).astype(bool)
    tax_col = "hi_tax_liability" if "hi_tax_liability" in df.columns else "hi_state_tax"

    actual_tax_m = float(
        (df.loc[mask, tax_col] * df.loc[mask, "weight"]).sum() / 1e6
    )
    if actual_tax_m <= 0:
        raise ValueError(
            f"Synthetic filer tax is {actual_tax_m:.1f}M — run _compute_base_tax() first."
        )

    k = target_tax_m / actual_tax_m

    income_cols = [
        c for c in [
            "income", "agi", "synthetic_total_income",
            "earned_income", "investment_income",
            "primary_wagp", "primary_intp",
        ]
        if c in df.columns
    ]

    out = df.copy()
    for col in income_cols:
        out.loc[mask, col] = out.loc[mask, col] * k

    # Clear stale tax so caller must recompute with updated incomes
    for col in ("hi_tax_liability", "hi_state_tax", "hi_agi", "hi_taxable_income",
                "hi_tax_before_credits"):
        if col in out.columns:
            out.loc[mask, col] = 0.0

    logger.info(
        "rescale_synthetic_tail: k=%.4f  actual_tax=%.1fM → target=%.1fM",
        k, actual_tax_m, target_tax_m,
    )
    return out, k


# ---------------------------------------------------------------------------
# Mid-high-income ($500K-$1M) income redistribution — corrects PUMS topcode
# compression while preserving PUMS demographics (filing status, household
# structure, geography). Replaces ``synthesize_mid_high_filers`` v1 which
# used DOTAX A8 filing-status shares (69% MFJ) and dropped the Single/MFS
# share that drives much of the HB 2306 surcharge revenue.
# ---------------------------------------------------------------------------
def redistribute_mid_high_incomes(
    df: pd.DataFrame,
    pareto_alpha: float = PARETO_ALPHA,
) -> pd.DataFrame:
    """Redistribute $500K-$1M filers' ``income`` to truncated Pareto.

    PUMS bottom-codes above-topcode incomes at the median above threshold,
    compressing $500K-$1M filers near $500K. This understates HB 2306
    surcharge revenue (which depends on income above $225K/$337.5K/$450K
    thresholds, scaling with how far each filer's income exceeds those).

    The function rewrites ``income`` for filers in [$500K, $1M) using a
    truncated Pareto inverse-CDF mapped to each filer's weighted percentile.
    All other columns (filing_status, hh_id, num_adults, weight, deductions,
    credits) are preserved — so the PUMS-actual filing-status mix (~62% MFJ
    / 26% MFS / 10% Single / 2.5% HoH for Hawaii $500K-$1M) carries through.

    Caller must re-run ``_compute_base_tax`` afterward to refresh tax columns.

    Parameters
    ----------
    df : DataFrame
        Tax units after IPF calibration. Must have ``income``, ``weight``.
    pareto_alpha : float
        Pareto shape, default 1.5 (IRS SOI 2022 Hawaii top-tail).

    Returns
    -------
    DataFrame with ``income`` (and any AGI alias columns) redistributed.
    """
    from tax_modeler.adjustments.mid_high_income_synthesizer import (
        redistribute_500k_to_1m_incomes,
    )
    out, _ = redistribute_500k_to_1m_incomes(df, pareto_alpha=pareto_alpha)

    # Stale tax columns must be recomputed after income changes
    mask = (out["income"] >= 500_000) & (out["income"] < 1_000_000)
    for col in ("hi_tax_liability", "hi_state_tax", "hi_agi", "hi_taxable_income",
                "hi_tax_before_credits"):
        if col in out.columns:
            out.loc[mask, col] = 0.0
    return out
