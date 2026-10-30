"""Capital gains share imputation for PUMS filers in the $100K–$1M range.

Extends ``synthetic_cg_share`` (set by top_income_synthesis for $1M+ filers
via Pareto / SOI Table 1.4 synthesis) to lower income bins using SOI data.

Why this matters
----------------
PUMS ACS data does not separately survey capital gains realizations. The
``income`` field (PINCP) nominally includes them, but we have no per-filer
signal for how much of a $150K filer's income is CG. Without a CG share,
``calculate_hawaii_tax_for_units`` and ``per_unit_tax`` treat all income as
ordinary, applying full bracket rates. The Hawaii CG alt-tax (HRS §235-51(f))
caps CG tax at 7.25%, so misallocating CG share materially distorts revenue.

CG share rises with income (IRS / DOTAX both confirm)
-----------------------------------------------------
National SOI Table 1.4 TY2022 CG/AGI shares::

    $200K-$500K:    5.1%
    $500K-$1M:     10.8%
    $1M-$1.5M:     15.6%
    $1.5M-$2M:     17.8%
    $2M-$5M:       23.3%
    $5M-$10M:      30.3%
    $10M+:         47.6%

A single flat share for all $200K-$1M filers (the previous implementation)
understates CG concentration at the $500K-$1M end and overstates CG-cap
relief for the $200K-$500K cohort, biasing top-bracket revenue estimates.

Scope
-----
- $100K-$200K: HI state SOI Table 2 share (uniform within bin).
- $200K-$500K: national SOI Table 1.4 share × HI calibration ratio.
- $500K-$1M:   national SOI Table 1.4 share × HI calibration ratio.
- $0-$100K:   excluded (IRS SOI <1% of AGI is CG).
- $1M+:       excluded (SOI Table 1.4 tier synthesis already sets per-tier shares).

The HI calibration ratio = (HI $200K+ share) / (national $200K+ share),
applied uniformly to both sub-bins to preserve the empirical HI/national
gap while keeping the national monotonicity by income.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Income thresholds that bound the imputation range
_LOWER_BOUND = 100_000.0   # below this: no imputation
_UPPER_BOUND = 1_000_000.0  # at/above this: handled by synthesis


def impute_capital_gains_from_soi(
    df: pd.DataFrame,
    soi_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Set ``synthetic_cg_share`` for PUMS filers in the $100K–$1M range.

    Parameters
    ----------
    df:
        Tax units DataFrame with at least ``income`` and ``weight``.
        Rows already having ``synthetic_cg_share > 0`` (Pareto synthesis)
        are skipped.
    soi_df:
        Output of ``load_soi_state_targets()``. Loaded automatically from
        the default CSV path if *None*.

    Returns
    -------
    pd.DataFrame — copy of *df* with ``synthetic_cg_share`` updated for the
    two mid-tier income bins.
    """
    from tax_modeler.calibration.irs_soi_state_targets import (
        SOI_AGI_BINS,
        load_soi_state_targets,
    )

    out = df.copy()

    if soi_df is None:
        try:
            soi_df = load_soi_state_targets()
        except FileNotFoundError as exc:
            logger.warning("SOI CSV not found — CG imputation skipped: %s", exc)
            return out

    # Normalise the CG share column
    if "synthetic_cg_share" not in out.columns:
        out["synthetic_cg_share"] = 0.0
    else:
        out["synthetic_cg_share"] = out["synthetic_cg_share"].fillna(0.0)

    incomes  = out["income"].to_numpy(dtype=float)
    _weights  = out["weight"].to_numpy(dtype=float)
    cg_arr   = out["synthetic_cg_share"].to_numpy(dtype=float).copy()

    # Rows already assigned by Pareto synthesis — do not overwrite.
    already_synthesized = cg_arr > 0

    # Look up the actual bin tuples from SOI_AGI_BINS to avoid float/int
    # mismatch in MultiIndex lookup.
    bin_100_200 = next(b for b in SOI_AGI_BINS if b[0] == 100_000)
    _bin_200_plus = next(b for b in SOI_AGI_BINS if b[0] == 200_000)

    # ── $100K–$200K bin ──────────────────────────────────────────────────────
    row_100_200 = soi_df.loc[bin_100_200]
    cg_100_200_M  = float(row_100_200["capgain_M"])
    agi_100_200_M = float(row_100_200["agi_M"])
    share_100_200 = cg_100_200_M / agi_100_200_M if agi_100_200_M > 0 else 0.0

    mask_100_200 = (
        (incomes >= 100_000) & (incomes < 200_000) & ~already_synthesized
    )

    # ── $200K–$500K and $500K–$1M (finer national bins, HI-calibrated) ───────
    # National SOI Table 1.4 has finer sub-$1M bins than HI state Table 2.
    # We use national bin-level CG/AGI shares to capture the income-rising
    # CG concentration, then scale by an HI/national ratio computed at the
    # $200K+ aggregate level (state Table 2 vs national Table 1.4 sum).
    nat_200_500_share, nat_500_1m_share, hi_calib_ratio = (
        _national_subbin_shares_with_hi_calibration(soi_df)
    )
    share_200k_500k = nat_200_500_share * hi_calib_ratio
    share_500k_1m   = nat_500_1m_share  * hi_calib_ratio

    mask_200k_500k = (
        (incomes >= 200_000) & (incomes < 500_000) & ~already_synthesized
    )
    mask_500k_1m = (
        (incomes >= 500_000) & (incomes < 1_000_000) & ~already_synthesized
    )

    # Apply
    cg_arr[mask_100_200]   = share_100_200
    cg_arr[mask_200k_500k] = share_200k_500k
    cg_arr[mask_500k_1m]   = share_500k_1m
    out["synthetic_cg_share"] = cg_arr

    logger.info(
        "CG imputation (income-rising): "
        "$100K–$200K=%.3f, $200K–$500K=%.3f, $500K–$1M=%.3f | "
        "national base (%.3f / %.3f) × HI calib ratio %.3f",
        share_100_200, share_200k_500k, share_500k_1m,
        nat_200_500_share, nat_500_1m_share, hi_calib_ratio,
    )

    return out


def _national_subbin_shares_with_hi_calibration(
    soi_state_df: pd.DataFrame,
    *,
    national_csv: Path | None = None,
) -> tuple[float, float, float]:
    """Return (national_200K-500K_share, national_500K-1M_share, HI_calib_ratio).

    The HI calibration ratio = HI $200K+ CG/AGI share ÷ national $200K+ CG/AGI
    share, computed from state SOI Table 2 ($200K+ aggregate) vs the sum of
    national Table 1.4 bins from $200K-$500K through $10M+. Applied uniformly
    to both sub-bin shares to preserve empirical HI/national levels while
    inheriting national monotonicity.
    """
    from tax_modeler.calibration.irs_soi_state_targets import SOI_AGI_BINS

    if national_csv is None:
        national_csv = (
            Path(__file__).resolve().parents[5]
            / "data" / "tax_modeler" / "irs_soi"
            / "national_soi_table_1_4_2022.csv"
        )

    nat = pd.read_csv(national_csv)

    def _bin(lo: float, hi: float) -> pd.Series:
        sel = (nat["agi_lo"] == lo) & (
            (nat["agi_hi"] == hi) if not np.isinf(hi) else nat["agi_hi"].isna()
        )
        # Some rows may store inf differently; fall back to >= lo
        if not sel.any():
            sel = nat["agi_lo"] >= lo
        return nat[sel].iloc[0]

    nat_200_500 = nat[(nat["agi_lo"] == 200_000) & (nat["agi_hi"] == 500_000)].iloc[0]
    nat_500_1m  = nat[(nat["agi_lo"] == 500_000) & (nat["agi_hi"] == 1_000_000)].iloc[0]
    nat_200_500_share = float(nat_200_500["capgain_M"] / nat_200_500["agi_less_deficit_M"])
    nat_500_1m_share  = float(nat_500_1m["capgain_M"]  / nat_500_1m["agi_less_deficit_M"])

    # National $200K+ aggregate share (sum all bins ≥ $200K)
    nat_200p = nat[nat["agi_lo"] >= 200_000]
    nat_200p_share = float(nat_200p["capgain_M"].sum() / nat_200p["agi_less_deficit_M"].sum())

    # HI $200K+ aggregate share from state SOI Table 2
    bin_200_plus = next(b for b in SOI_AGI_BINS if b[0] == 200_000)
    hi_row = soi_state_df.loc[bin_200_plus]
    hi_200p_share = float(hi_row["capgain_M"] / hi_row["agi_M"]) if hi_row["agi_M"] > 0 else 0.0

    calib_ratio = hi_200p_share / nat_200p_share if nat_200p_share > 0 else 1.0
    return nat_200_500_share, nat_500_1m_share, calib_ratio
