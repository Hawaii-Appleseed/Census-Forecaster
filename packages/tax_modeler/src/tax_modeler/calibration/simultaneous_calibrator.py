"""
Simultaneous Two-Phase Calibrator

Replaces the fragile 6-stage sequential calibration with a single-pass approach:

Phase 1 — Entropy-balanced reweighting
    Find weights that simultaneously match ALL filer-count targets across every
    (filing_status x AGI_bracket) cell, while staying close to the original PUMS
    weights.  Uses iterative raking (multiplicative IPF) with bounded weight ratios.

Phase 2 — Direct bracket tax multipliers
    After weights are locked, compute one scalar multiplier per AGI bracket so
    that weighted tax matches the DOTAX Table A8 target exactly.

Design principles:
    - All constraints are satisfied in a single pass (no stages undoing each other)
    - No arbitrary ±60% caps, no 70% blending, no hardcoded marginal rates
    - Deductions must be folded into tax BEFORE this calibrator runs
    - Synthetic ultra-high filers must be present BEFORE this calibrator runs
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from tax_modeler.calibration import dotax_base

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Canonical DOTAX targets (Table A-8 and Table 4, newest edition on file)
# ---------------------------------------------------------------------------
# One copy, in ``dotax_base`` (TY2023: 631,125 filers, $2,961M tax).

DOTAX_FILER_TARGETS: Dict[Tuple[float, float], int] = dotax_base.filer_targets()
DOTAX_TAX_TARGETS: Dict[Tuple[float, float], float] = dotax_base.tax_targets_M()
DOTAX_STATUS_TARGETS: Dict[str, int] = dotax_base.status_targets()

AGI_BRACKETS = sorted(DOTAX_FILER_TARGETS.keys())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bracket_index(agi: float) -> int:
    """Return the index into AGI_BRACKETS for a given AGI value."""
    for i, (lo, hi) in enumerate(AGI_BRACKETS):
        if lo <= agi < hi:
            return i
    return len(AGI_BRACKETS) - 1


def _bracket_label(lo: float, hi: float) -> str:
    if hi == np.inf:
        return f"${lo / 1000:.0f}k+"
    return f"${lo / 1000:.0f}k–${hi / 1000:.0f}k"


# ---------------------------------------------------------------------------
# Phase 1: Entropy-balanced reweighting via iterative raking
# ---------------------------------------------------------------------------

def phase1_reweight(
    df: pd.DataFrame,
    max_iterations: int = 100,
    tolerance: float = 0.005,
    weight_floor: float = 0.05,
    weight_ceiling_ratio: float = 15.0,
    *,
    filer_targets: Optional[Dict[Tuple[float, float], int]] = None,
    status_targets: Optional[Dict[str, int]] = None,
    agi_mass_targets: Optional[Dict[Tuple[float, float], float]] = None,
    tier_agi_targets: Optional[Dict[Tuple[float, float], float]] = None,
) -> pd.DataFrame:
    """
    Adjust weights to simultaneously match DOTAX-style filer-count targets
    across multiple margins.

    Margins:
        A) AGI-bracket filer counts (always)
        B) Filing-status totals (always)
        C) AGI-bracket aggregate AGI mass ($M) — when ``agi_mass_targets`` given
        D) SOI tier aggregate AGI mass ($M) — when ``tier_agi_targets`` given;
           applies WITHIN $1M+ filers, identified by ``synthetic_tier_lo`` column

    Uses iterative raking (multiplicative IPF). With AGI-mass margins active,
    this implements the CBO/TPC-standard "stage-2 reweighting to forward
    distributional targets" — preventing cohort-aged $1M+ income from
    compounding indefinitely (Auten/Gee/Turner 2013 mobility findings).

    Args:
        df: Tax-unit DataFrame with columns 'agi', 'filing_status', 'weight'.
            For tier reweighting, also requires 'synthetic_tier_lo'.
        max_iterations: Maximum raking iterations.
        tolerance: Convergence when every marginal is within this fraction of target.
        weight_floor: Minimum allowed weight (prevents zeros).
        weight_ceiling_ratio: Max ratio of calibrated-to-original weight.
        filer_targets: Optional override for AGI-bracket filer counts.
        status_targets: Optional override for filing-status totals.
        agi_mass_targets: ``{(agi_lo, agi_hi): aggregate_AGI_M}`` for stage-2
            reweighting. Default ``None`` disables AGI-mass margin.
        tier_agi_targets: ``{(tier_lo, tier_hi): aggregate_AGI_M}`` for the
            5 SOI Table 1.4 tiers within $1M+. Default ``None`` disables.

    Returns:
        DataFrame with updated 'weight' column and 'weight_original' preserving
        the pre-calibration values.
    """
    fts = filer_targets if filer_targets is not None else DOTAX_FILER_TARGETS
    sts = status_targets if status_targets is not None else DOTAX_STATUS_TARGETS
    brackets = sorted(fts.keys())

    result = df.copy()
    if 'weight_original' not in result.columns:
        result['weight_original'] = result['weight'].copy()

    # Pre-compute bracket assignment (integer index for speed)
    def _idx(agi: float) -> int:
        for i, (lo, hi) in enumerate(brackets):
            if lo <= agi < hi:
                return i
        return len(brackets) - 1
    result['_bracket_idx'] = result['agi'].apply(_idx)

    # Map filing status to integer for fast groupby
    status_list = sorted(sts.keys())
    status_to_int = {s: i for i, s in enumerate(status_list)}
    result['_status_idx'] = result['filing_status'].map(status_to_int)

    # Drop rows that don't map to a known filing status
    unmapped = result['_status_idx'].isna()
    if unmapped.any():
        logger.warning(
            f"Dropping {unmapped.sum()} units with unrecognised filing_status "
            f"values: {result.loc[unmapped, 'filing_status'].unique().tolist()}"
        )
        result = result[~unmapped].copy()
        result['_status_idx'] = result['_status_idx'].astype(int)

    # Upper bound on weight per unit (relative to the pre-rake weight)
    weight_caps = result['weight_original'] * weight_ceiling_ratio

    # Pre-compute AGI array for AGI-mass margins
    agi_array = result['agi'].to_numpy(dtype=float)

    # Pre-compute synthetic tier index for tier-mass margin
    tier_keys: list = []
    if tier_agi_targets is not None and 'synthetic_tier_lo' in result.columns:
        tier_keys = sorted(tier_agi_targets.keys())
        tier_lo_array = result['synthetic_tier_lo'].fillna(-1).to_numpy(dtype=float)
        tier_idx = np.full(len(result), -1, dtype=int)
        for i, (lo, _hi) in enumerate(tier_keys):
            tier_idx[tier_lo_array == lo] = i
        result['_tier_idx'] = tier_idx

    converged = False
    for iteration in range(1, max_iterations + 1):
        max_deviation = 0.0

        # --- Margin A: AGI bracket filer counts ---
        for b_idx, (lo, hi) in enumerate(brackets):
            mask = result['_bracket_idx'] == b_idx
            current = result.loc[mask, 'weight'].sum()
            target = fts[(lo, hi)]
            if current > 0 and target > 0:
                factor = target / current
                result.loc[mask, 'weight'] *= factor
                max_deviation = max(max_deviation, abs(factor - 1.0))

        # --- Margin B: Filing status totals ---
        for s_name in status_list:
            s_idx = status_to_int[s_name]
            mask = result['_status_idx'] == s_idx
            current = result.loc[mask, 'weight'].sum()
            target = sts[s_name]
            if current > 0 and target > 0:
                factor = target / current
                result.loc[mask, 'weight'] *= factor
                max_deviation = max(max_deviation, abs(factor - 1.0))

        # --- Margin C: AGI-mass per AGI bracket (stage-2 reweighting) ---
        # When tier targets are active, skip the $1M+ bracket here — its
        # AGI mass is fully determined by the per-tier targets (margin D).
        # Imposing it twice causes IPF oscillation (count×avg ≠ mass).
        if agi_mass_targets is not None:
            weights = np.array(result['weight'].to_numpy(dtype=float), copy=True)
            for b_idx, (lo, hi) in enumerate(brackets):
                if (lo, hi) not in agi_mass_targets:
                    continue
                if tier_keys and lo >= 1_000_000:
                    continue
                mask = (result['_bracket_idx'] == b_idx).to_numpy()
                current_M = float((agi_array[mask] * weights[mask]).sum() / 1e6)
                target_M = agi_mass_targets[(lo, hi)]
                if current_M > 0 and target_M > 0:
                    factor = target_M / current_M
                    weights[mask] *= factor
                    max_deviation = max(max_deviation, abs(factor - 1.0))
            result['weight'] = weights

        # --- Margin D: SOI tier AGI-mass within $1M+ ---
        if tier_keys:
            weights = np.array(result['weight'].to_numpy(dtype=float), copy=True)
            for t_idx, (lo, hi) in enumerate(tier_keys):
                mask = (result['_tier_idx'] == t_idx).to_numpy()
                if not mask.any():
                    continue
                current_M = float((agi_array[mask] * weights[mask]).sum() / 1e6)
                target_M = tier_agi_targets[(lo, hi)]
                if current_M > 0 and target_M > 0:
                    factor = target_M / current_M
                    weights[mask] *= factor
                    max_deviation = max(max_deviation, abs(factor - 1.0))
            result['weight'] = weights

        # --- Enforce bounds ---
        result['weight'] = result['weight'].clip(lower=weight_floor, upper=weight_caps)

        if iteration <= 3 or iteration % 10 == 0:
            total_w = result['weight'].sum()
            logger.info(
                f"  Raking iteration {iteration:>3d}: max_deviation={max_deviation:.4f}  "
                f"total_filers={total_w:,.0f}"
            )

        if max_deviation < tolerance:
            converged = True
            break

    if converged:
        logger.info(f"  Phase 1 converged after {iteration} iterations (tol={tolerance})")
    else:
        logger.warning(
            f"  Phase 1 did NOT converge after {max_iterations} iterations "
            f"(max_deviation={max_deviation:.4f}, tol={tolerance})"
        )

    # --- Report final match quality ---
    logger.info("\n  Phase 1 — Filer count match by AGI bracket:")
    for b_idx, (lo, hi) in enumerate(brackets):
        mask = result['_bracket_idx'] == b_idx
        actual = result.loc[mask, 'weight'].sum()
        target = fts[(lo, hi)]
        pct = (actual / target - 1) * 100 if target > 0 else 0
        label = _bracket_label(lo, hi)
        logger.info(f"    {label:<18s}  {actual:>10,.0f} vs {target:>10,d}  ({pct:>+5.1f}%)")

    logger.info("\n  Phase 1 — Filer count match by filing status:")
    for s_name in status_list:
        s_idx = status_to_int[s_name]
        mask = result['_status_idx'] == s_idx
        actual = result.loc[mask, 'weight'].sum()
        target = sts[s_name]
        pct = (actual / target - 1) * 100 if target > 0 else 0
        logger.info(f"    {s_name:<30s}  {actual:>10,.0f} vs {target:>10,d}  ({pct:>+5.1f}%)")

    # Report AGI-mass and tier match if active
    if agi_mass_targets is not None:
        logger.info("\n  Phase 1 — AGI mass match by AGI bracket (stage-2 reweighting):")
        weights = result['weight'].to_numpy(dtype=float)
        for b_idx, (lo, hi) in enumerate(brackets):
            if (lo, hi) not in agi_mass_targets:
                continue
            mask = (result['_bracket_idx'] == b_idx).to_numpy()
            actual = float((agi_array[mask] * weights[mask]).sum() / 1e6)
            target = agi_mass_targets[(lo, hi)]
            pct = (actual / target - 1) * 100 if target > 0 else 0
            label = _bracket_label(lo, hi)
            logger.info(f"    {label:<18s}  ${actual:>10,.0f}M vs ${target:>10,.0f}M  ({pct:>+5.1f}%)")

    if tier_keys:
        logger.info("\n  Phase 1 — AGI mass match by SOI tier ($1M+):")
        weights = result['weight'].to_numpy(dtype=float)
        for t_idx, (lo, hi) in enumerate(tier_keys):
            mask = (result['_tier_idx'] == t_idx).to_numpy()
            if not mask.any():
                continue
            actual = float((agi_array[mask] * weights[mask]).sum() / 1e6)
            target = tier_agi_targets[(lo, hi)]
            pct = (actual / target - 1) * 100 if target > 0 else 0
            label = _bracket_label(lo, hi)
            logger.info(f"    {label:<18s}  ${actual:>10,.0f}M vs ${target:>10,.0f}M  ({pct:>+5.1f}%)")

    drop_cols = ['_bracket_idx', '_status_idx']
    if '_tier_idx' in result.columns:
        drop_cols.append('_tier_idx')
    result.drop(columns=drop_cols, inplace=True)
    # Convergence record for the calibration report (year_recalibrator reads
    # it); a non-converged rake used to be a log line only.
    result.attrs["phase1_rake"] = {
        "converged": bool(converged),
        "iterations": int(iteration),
        "max_iterations": int(max_iterations),
        "tolerance": float(tolerance),
        "final_max_dev": float(max_deviation),
    }
    return result


# ---------------------------------------------------------------------------
# Phase 2: Direct bracket tax multipliers
# ---------------------------------------------------------------------------

def phase2_tax_calibrate(
    df: pd.DataFrame,
    tax_col: str = 'hi_state_tax',
    weight_col: str = 'weight',
    multiplier_floor: float = 0.3,
    multiplier_ceiling: float = 5.0,
    *,
    tax_targets: Optional[Dict[Tuple[float, float], float]] = None,
) -> pd.DataFrame:
    """
    Scale per-unit tax liabilities so that the weighted total in every AGI
    bracket matches a DOTAX-style target.

    This is a single-pass, exact calibration: one scalar multiplier per
    bracket.  No iteration, no blending, no caps that prevent convergence.

    Multiplier bounds (floor/ceiling) prevent pathological results in
    brackets where the model tax is near-zero, but they are wide enough
    that well-populated brackets will hit their target exactly.

    Args:
        df: DataFrame with per-unit tax and weight columns.
        tax_col: Name of the tax liability column.
        weight_col: Name of the weight column.
        multiplier_floor: Minimum allowed tax multiplier.
        multiplier_ceiling: Maximum allowed tax multiplier.
        tax_targets: Optional override for per-bracket tax-target dict
            ``{(lo, hi): tax_M}``. Default: TY2022 DOTAX targets.

    Returns:
        DataFrame with adjusted tax column and new 'tax_multiplier' column.
    """
    tts = tax_targets if tax_targets is not None else DOTAX_TAX_TARGETS
    brackets = sorted(tts.keys())

    result = df.copy()
    result['tax_multiplier'] = 1.0

    def _idx(agi: float) -> int:
        for i, (lo, hi) in enumerate(brackets):
            if lo <= agi < hi:
                return i
        return len(brackets) - 1
    result['_bracket_idx'] = result['agi'].apply(_idx)

    total_model = 0.0
    total_target = 0.0

    logger.info("\n  Phase 2 — Tax calibration by AGI bracket:")

    for b_idx, (lo, hi) in enumerate(brackets):
        mask = result['_bracket_idx'] == b_idx
        n_units = mask.sum()
        if n_units == 0:
            continue

        model_tax_m = (
            result.loc[mask, tax_col] * result.loc[mask, weight_col]
        ).sum() / 1_000_000
        target_tax_m = tts[(lo, hi)]

        if model_tax_m > 0:
            raw_mult = target_tax_m / model_tax_m
            mult = np.clip(raw_mult, multiplier_floor, multiplier_ceiling)
        else:
            mult = 1.0

        result.loc[mask, tax_col] *= mult
        result.loc[mask, 'tax_multiplier'] = mult

        new_tax_m = (
            result.loc[mask, tax_col] * result.loc[mask, weight_col]
        ).sum() / 1_000_000

        total_model += new_tax_m
        total_target += target_tax_m

        label = _bracket_label(lo, hi)
        status = "ok" if abs(new_tax_m / target_tax_m - 1) < 0.02 else "CAPPED"
        logger.info(
            f"    {label:<18s}  model ${new_tax_m:>7.1f}M  target ${target_tax_m:>7.1f}M  "
            f"x{mult:.3f}  [{status}]"
        )

    gap_pct = (total_model / total_target - 1) * 100 if total_target > 0 else 0
    logger.info(
        f"\n    TOTAL            model ${total_model:>7.1f}M  target ${total_target:>7.1f}M  "
        f"({gap_pct:>+.1f}%)"
    )

    result.drop(columns=['_bracket_idx'], inplace=True)
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SimultaneousCalibrator:
    """
    Two-phase simultaneous calibration.

    Usage::

        calibrator = SimultaneousCalibrator()
        tax_units = calibrator.calibrate(tax_units)

    Prerequisites (must be done BEFORE calling calibrate):
        1. Synthetic ultra-high filers already appended
        2. Tax calculated for all units (including deductions)
        3. Columns present: 'agi', 'filing_status', 'weight', 'hi_state_tax'
    """

    def __init__(
        self,
        raking_max_iter: int = 100,
        raking_tolerance: float = 0.005,
        weight_ceiling_ratio: float = 15.0,
        tax_multiplier_ceiling: float = 5.0,
        *,
        filer_targets: Optional[Dict[Tuple[float, float], int]] = None,
        status_targets: Optional[Dict[str, int]] = None,
        tax_targets: Optional[Dict[Tuple[float, float], float]] = None,
    ):
        self.raking_max_iter = raking_max_iter
        self.raking_tolerance = raking_tolerance
        self.weight_ceiling_ratio = weight_ceiling_ratio
        self.tax_multiplier_ceiling = tax_multiplier_ceiling
        self.filer_targets = filer_targets
        self.status_targets = status_targets
        self.tax_targets = tax_targets

    def calibrate(self, df: pd.DataFrame) -> pd.DataFrame:
        """Run both calibration phases and return the calibrated DataFrame."""
        required = {'agi', 'filing_status', 'weight', 'hi_state_tax'}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Missing required columns: {missing}")

        logger.info("=" * 72)
        logger.info("SIMULTANEOUS TWO-PHASE CALIBRATION")
        logger.info("=" * 72)

        pre_filers = df['weight'].sum()
        pre_tax = (df['hi_state_tax'] * df['weight']).sum() / 1_000_000
        logger.info(f"  Input: {len(df):,} units, {pre_filers:,.0f} weighted filers, ${pre_tax:,.1f}M tax")

        # Phase 1: reweight to match filer counts
        logger.info("\n--- Phase 1: Entropy-balanced reweighting (raking) ---")
        result = phase1_reweight(
            df,
            max_iterations=self.raking_max_iter,
            tolerance=self.raking_tolerance,
            weight_ceiling_ratio=self.weight_ceiling_ratio,
            filer_targets=self.filer_targets,
            status_targets=self.status_targets,
        )

        mid_filers = result['weight'].sum()
        mid_tax = (result['hi_state_tax'] * result['weight']).sum() / 1_000_000
        logger.info(f"\n  After Phase 1: {mid_filers:,.0f} weighted filers, ${mid_tax:,.1f}M tax")

        # Phase 2: bracket tax multipliers
        logger.info("\n--- Phase 2: Direct bracket tax multipliers ---")
        result = phase2_tax_calibrate(
            result,
            multiplier_ceiling=self.tax_multiplier_ceiling,
            tax_targets=self.tax_targets,
        )

        post_filers = result['weight'].sum()
        post_tax = (result['hi_state_tax'] * result['weight']).sum() / 1_000_000
        target_total = sum((self.tax_targets or DOTAX_TAX_TARGETS).values())
        gap = (post_tax / target_total - 1) * 100 if target_total > 0 else 0.0

        logger.info("\n" + "=" * 72)
        logger.info(
            f"  FINAL: {post_filers:,.0f} weighted filers, ${post_tax:,.1f}M tax  "
            f"({gap:>+.1f}% vs ${target_total:,.0f}M target)"
        )
        logger.info("=" * 72)

        return result
