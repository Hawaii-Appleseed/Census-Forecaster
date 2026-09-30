"""Top-income (1M+) synthesis wrapper for the SB3125 CD1 fiscal-impact pipeline.

PUMS samples ultra-high-income filers very thinly: the calibrated tax units
recover only ~19% of the DOTAX $1M+ filer count (342 weighted vs the 1,824
target, DOTAX TY2022 Table A-8). The IPF rake's
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

Then :func:`calibrate_synthetic_tail_to_tax_target` scales the tail's
incomes until its re-scored tax is the DOTAX target, and
:func:`age_synthetic_tail` moves it from the target's tax year (TY2022) to
the PUMS income dollar year the projection grows every unit from.

DOTAX target source: DOTAX TY2022 Table A-8 (1,824 resident returns above
$1M AGI owing $662.6M before credits; ``forward_targets``'
``_DOTAX_*_TARGETS_2022`` and ``calibration.cg_anchor`` use the same
figures).
"""
from __future__ import annotations

import logging
from typing import Callable, Dict

import pandas as pd

from tax_modeler.loaders.pums_loader import PUMS_INCOME_DOLLAR_YEAR

logger = logging.getLogger(__name__)

# DOTAX TY2022 Hawaii target for $1M+ AGI bracket (Table A-8)
DOTAX_1M_PLUS_FILER_TARGET = 1_824
DOTAX_1M_PLUS_TAX_TARGET_M = 663.0   # $ millions
# The tax year the two targets above describe: a tail calibrated to them is
# in TY2022 dollars (see age_synthetic_tail).
DOTAX_1M_PLUS_TARGET_TAX_YEAR = 2022
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

    # Federal AGI / earned income were copied from template rows; rebuild
    # them from the overridden components.
    if synth_mask.any() and "federal_agi" in result.columns:
        from tax_modeler.liability.federal import add_federal_income_columns

        fed = add_federal_income_columns(result.loc[synth_mask])
        for col in ("federal_agi", "federal_earned_income"):
            result.loc[synth_mask, col] = fed[col]

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


# Income columns scaled on synthetic rows, by the tax rescale and the aging.
_TAIL_INCOME_COLUMNS = (
    "income", "agi", "synthetic_total_income",
    "earned_income", "investment_income",
    "primary_wagp", "primary_intp",
)
_TAIL_STALE_TAX_COLUMNS = (
    "hi_tax_liability", "hi_state_tax", "hi_agi", "hi_taxable_income",
    "hi_tax_before_credits",
)


def _synthetic_mask(df: pd.DataFrame) -> pd.Series:
    return df["is_synthetic_ultra_high"].fillna(False).astype(bool)


def _synthetic_tail_tax_m(df: pd.DataFrame) -> float:
    """Weighted Hawaii tax on the synthetic $1M+ rows, $M."""
    mask = _synthetic_mask(df)
    tax_col = "hi_tax_liability" if "hi_tax_liability" in df.columns else "hi_state_tax"
    return float((df.loc[mask, tax_col] * df.loc[mask, "weight"]).sum() / 1e6)


def _scale_synthetic_incomes(df: pd.DataFrame, factor: float) -> pd.DataFrame:
    """``df`` with the synthetic rows' income columns multiplied by ``factor``,
    their federal income columns rebuilt from them (as
    :func:`synthesize_top_filers` builds them) and their tax columns cleared:
    re-score afterwards."""
    mask = _synthetic_mask(df)
    out = df.copy()
    for col in _TAIL_INCOME_COLUMNS:
        if col in out.columns:
            out.loc[mask, col] = out.loc[mask, col] * factor
    if mask.any() and "federal_agi" in out.columns:
        from tax_modeler.liability.federal import add_federal_income_columns

        fed = add_federal_income_columns(out.loc[mask])
        for col in ("federal_agi", "federal_earned_income"):
            out.loc[mask, col] = fed[col]
    # Clear stale tax so caller must recompute with updated incomes
    for col in _TAIL_STALE_TAX_COLUMNS:
        if col in out.columns:
            out.loc[mask, col] = 0.0
    return out


def rescale_synthetic_tail_to_tax_target(
    df: pd.DataFrame,
    target_tax_m: float = DOTAX_1M_PLUS_TAX_TARGET_M,
) -> tuple:
    """One proportional step toward the tax target: scale synthesized $1M+
    filer incomes uniformly by ``k = target_tax_m / actual``.

    This closes the gap between Pareto-distribution-derived income levels and
    the DOTAX tax benchmark.  The Pareto synthesizer hits the filer
    count target exactly but typically recovers only ~88% of the $663M tax
    target because the conditional-mean income per tier underestimates the
    very top of the tail.  A uniform income scale factor ``k`` applied to all
    synthetic filer incomes corrects this.

    One step does not land on the target. Tax is not proportional to income
    even above $1M: the deduction, the lower brackets and the §235-51(f)
    alternative tax on gains make it roughly ``a·k − b``, so re-scoring after
    this step misses in the direction of the step (the Act 24 MID tail:
    $686.9M against $663M, k 1.4685 where 1.4210 lands). Use
    :func:`calibrate_synthetic_tail_to_tax_target`, which iterates to the
    target; this is its first step.

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
    actual_tax_m = _synthetic_tail_tax_m(df)
    if actual_tax_m <= 0:
        raise ValueError(
            f"Synthetic filer tax is {actual_tax_m:.1f}M — run _compute_base_tax() first."
        )

    k = target_tax_m / actual_tax_m
    out = _scale_synthetic_incomes(df, k)

    logger.info(
        "rescale_synthetic_tail: k=%.4f  actual_tax=%.1fM → target=%.1fM",
        k, actual_tax_m, target_tax_m,
    )
    return out, k


def calibrate_synthetic_tail_to_tax_target(
    units: pd.DataFrame,
    *,
    score: Callable[[pd.DataFrame], pd.DataFrame],
    target_tax_m: float = DOTAX_1M_PLUS_TAX_TARGET_M,
    rtol: float = 1e-3,
    max_iter: int = 8,
) -> tuple[pd.DataFrame, float]:
    """Scale the synthetic $1M+ tail's incomes until its re-scored Hawaii tax
    is ``target_tax_m`` within ``rtol``; return ``(units, k)``.

    Alternates scaling and re-scoring: the first step is
    :func:`rescale_synthetic_tail_to_tax_target`'s proportional ``k``, which
    misses because tax is not proportional to income; later steps are secant
    updates on the cumulative ``k``, which land within 0.1% in two or three
    re-scores.

    ``units`` must already be scored, and ``score`` must re-score a frame on
    the same deduction basis, e.g. ``lambda u: compute_base_tax(u,
    deduction_params=ded_params, tax_year=cal_tax_year)``. The iterations
    score the synthetic rows alone (scoring the whole frame takes seconds),
    so ``score`` must score each row independently, as ``compute_base_tax``
    does; the result is then scaled and scored whole once, and checked.

    Returns the re-scored frame and ``k``, the product of the steps. Raises
    RuntimeError if the tail is not within ``rtol`` of the target after
    ``max_iter`` steps.
    """
    def off(tax: float) -> bool:
        return abs(tax / target_tax_m - 1.0) > rtol

    tail = units.loc[_synthetic_mask(units)]
    tax = _synthetic_tail_tax_m(tail)
    if tax <= 0:
        raise ValueError(
            f"Synthetic filer tax is {tax:.1f}M — score the units first."
        )
    k, prev = 1.0, None
    for _ in range(max_iter):
        if not off(tax):
            break
        k_next = None
        if prev is not None and tax != prev[1]:
            k_next = k + (target_tax_m - tax) * (k - prev[0]) / (tax - prev[1])
        if k_next is None or k_next <= 0:
            k_next = k * target_tax_m / tax          # the proportional step
        prev = (k, tax)
        tail = score(_scale_synthetic_incomes(tail, k_next / k))
        k, tax = k_next, _synthetic_tail_tax_m(tail)
    if off(tax):
        raise RuntimeError(
            f"Synthetic $1M+ tail tax ${tax:.1f}M is not within {rtol:.2%} of "
            f"${target_tax_m:.1f}M after {max_iter} steps (k={k:.4f})."
        )

    out = score(_scale_synthetic_incomes(units, k))
    whole = _synthetic_tail_tax_m(out)
    if off(whole):
        raise RuntimeError(
            f"Synthetic $1M+ tail tax ${whole:.1f}M scored with the whole frame, "
            f"${tax:.1f}M scored alone: score must score each row independently."
        )
    logger.info(
        "calibrate_synthetic_tail: k=%.4f  tax=%.1fM (target %.1fM)",
        k, whole, target_tax_m,
    )
    return out, k


def _observed_honolulu_b19013(year: int) -> float:
    """Honolulu's observed 1-year ACS B19013 for ``year``: the bundled
    calibration panel observation ``revenue_projection.project_revenue_per_filer``
    anchors the projector's county growth on."""
    from census_forecaster.acs.projection import effective_year

    from tax_modeler.projection.income_forecast import _load_b19013_series
    from tax_modeler.projection.tax_unit_projector import _HAWAII_COUNTY_GEOIDS

    geoid = _HAWAII_COUNTY_GEOIDS[HONOLULU_COUNTY]
    obs = [o for o in _load_b19013_series(geoid)
           if o.vintage == "1y" and effective_year(o) == year]
    if not obs:
        raise ValueError(f"No observed 1-year B19013 for {geoid} in {year} in the bundled panel.")
    return float(obs[-1].estimate)


def synthetic_tail_aging_factor(
    top_premium: float,
    *,
    year: int = PUMS_INCOME_DOLLAR_YEAR,
) -> float:
    """Growth of the synthetic $1M+ tail from the DOTAX targets' tax year
    (``DOTAX_1M_PLUS_TARGET_TAX_YEAR``) to ``year``:

        B19013_Honolulu(year) / B19013_Honolulu(2022) × (1 + top_premium) ** (year − 2022)

    The two growth steps the projection gives a Honolulu filer above $500K,
    over the years before its own base year: the county's median-income
    growth (observed here, where the projector forecasts it) and the
    scenario's top-income premium (``apply_top_income_growth_premium``).
    """
    base = DOTAX_1M_PLUS_TARGET_TAX_YEAR
    return (_observed_honolulu_b19013(year) / _observed_honolulu_b19013(base)
            * (1.0 + top_premium) ** (year - base))


def age_synthetic_tail(
    df: pd.DataFrame,
    *,
    top_premium: float,
    year: int = PUMS_INCOME_DOLLAR_YEAR,
) -> pd.DataFrame:
    """Move the tax-calibrated synthetic $1M+ tail from TY2022 dollars to
    ``year`` (the PUMS income dollar year); re-score afterwards.

    Why: :func:`calibrate_synthetic_tail_to_tax_target` puts the tail at the
    DOTAX TY2022 $1M+ tax (``DOTAX_1M_PLUS_TARGET_TAX_YEAR``), but every PUMS
    unit is in ``PUMS_INCOME_DOLLAR_YEAR`` (2024) dollars and the projection
    grows every unit from there: the county B19013 factor from the
    projector's 2024 anchor, the top-income premium from
    ``TOP_INCOME_PREMIUM_BASE_YEAR`` (2024). Left at the TY2022 level the tail
    never receives 2022-2024 growth, while the same pipeline ages DOTAX's
    TY2022 capital gains from 2022 (``cg_anchor.cg_growth``). The tail is
    Honolulu's, so it grows by :func:`synthetic_tail_aging_factor`: Honolulu's
    observed B19013 growth times the scenario's premium.

    Scales the income columns the tax rescale scales, rebuilds the federal
    income columns and clears the tax columns, on the synthetic rows only.
    """
    return _scale_synthetic_incomes(df, synthetic_tail_aging_factor(top_premium, year=year))


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
