"""Mid-High-Income Income Redistribution ($500K-$1M)

PUMS topcodes person income at the state-specific 99.5 percentile and replaces
above-topcode values with the median above the threshold. For Hawaii this
compresses $500K-$1M filers near $500K-$600K, leaving HB 2306 surcharge
revenue understated (the surcharge depends on income above $225K/$337.5K/$450K
thresholds, which scales with income beyond the bracket floor).

This module redistributes existing PUMS filers' incomes within $500K-$1M
to match a truncated Pareto distribution (alpha~1.5 anchored at $500K, capped
just below $1M to avoid double-counting with the $1M+ synthesizer). It does
NOT add or remove rows — only the ``income`` column changes — preserving the
PUMS-actual filing status mix, household structure, and demographics.

Why redistribute vs replace:
    The previous "replace with synthetic rows" approach used DOTAX $400K+
    filing-status shares (69% MFJ / 22% Single), but actual PUMS in
    $500K-$1M has ~62% MFJ / 26% MFS / 10% Single / 2.5% HoH. Because
    Hawaii MFS uses the same brackets as Single ($225K surcharge floor),
    the PUMS distribution generates ~36% surcharge-vulnerable filers vs
    the synth template's 26%. Overriding with template shares dropped
    HB 2306 surcharge revenue. Redistribution preserves the real mix.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


_DEFAULT_ALPHA = 1.5
_LO = 500_000.0
_HI = 999_999.0    # cap below $1M to avoid spillover into ultra-high tier


def _truncated_pareto_quantile(p: np.ndarray, alpha: float, lo: float, hi: float) -> np.ndarray:
    """Inverse CDF of truncated Pareto on [lo, hi] with shape ``alpha``.

    Untruncated Pareto: F(x) = 1 - (lo/x)^alpha for x >= lo.
    Truncated to [lo, hi]: F̃(x) = F(x) / F(hi).
    Inverse: x = lo / (1 - p * F(hi))^(1/alpha).
    """
    F_hi = 1.0 - (lo / hi) ** alpha
    return lo / (1.0 - p * F_hi) ** (1.0 / alpha)


def redistribute_500k_to_1m_incomes(
    df: pd.DataFrame,
    pareto_alpha: float = _DEFAULT_ALPHA,
    income_col: str = "income",
    weight_col: str = "weight",
) -> tuple[pd.DataFrame, dict]:
    """Replace ``income`` for $500K-$1M filers with a truncated-Pareto draw.

    The mapping uses each filer's weighted percentile within the bracket as
    the Pareto quantile. Sort order is preserved (low income → low Pareto
    draw, high income → high Pareto draw), so demographics correlated with
    income (e.g. occupation, age) stay aligned with their relative position.

    Parameters
    ----------
    df : DataFrame
        Tax units with ``income`` and ``weight`` columns. ``filing_status``,
        ``hh_id``, ``num_adults`` etc. are preserved unchanged.
    pareto_alpha : float
        Pareto shape parameter; 1.5 matches IRS SOI 2022 Hawaii top-tail.

    Returns
    -------
    (DataFrame, dict)
        Updated df (copy) plus diagnostics: pre/post mean income, pre/post
        median income, filer count, and the alpha used.
    """
    out = df.copy()
    mask = (out[income_col] >= _LO) & (out[income_col] < 1_000_000)
    if not mask.any():
        logger.warning("redistribute_500k_to_1m_incomes: no rows in $500K-$1M; skipping")
        return out, {"alpha": pareto_alpha, "n_filers": 0.0}

    sub = out.loc[mask].copy()
    # Stable sort by current income → preserves demographic-income correlations
    sub = sub.sort_values(income_col, kind="mergesort")
    weights = sub[weight_col].to_numpy(dtype=float)
    total_w = weights.sum()
    if total_w <= 0:
        logger.warning("redistribute_500k_to_1m_incomes: total weight zero; skipping")
        return out, {"alpha": pareto_alpha, "n_filers": 0.0}

    # Midpoint cumulative-weight percentile for each filer
    cum_w = np.cumsum(weights)
    pct = (cum_w - weights / 2.0) / total_w

    new_incomes = _truncated_pareto_quantile(pct, pareto_alpha, _LO, _HI)
    new_incomes = np.clip(new_incomes, _LO, _HI)

    # Diagnostics
    pre_mean = float((sub[income_col].to_numpy() * weights).sum() / total_w)
    post_mean = float((new_incomes * weights).sum() / total_w)
    pre_median_idx = np.searchsorted(cum_w, total_w / 2.0)
    pre_median_idx = min(pre_median_idx, len(sub) - 1)
    pre_median = float(sub[income_col].iloc[pre_median_idx])
    post_median = float(new_incomes[pre_median_idx])

    # Apply: write back in the original (unsorted) order
    sub[income_col] = new_incomes
    out.loc[sub.index, income_col] = sub[income_col]

    # Mirror onto AGI-adjacent columns the rest of the pipeline reads
    for alias in ("agi", "agi_with_cap_gains", "agi_without_cap_gains"):
        if alias in out.columns:
            out.loc[sub.index, alias] = sub[income_col]

    diags = {
        "alpha": pareto_alpha,
        "n_filers": float(total_w),
        "pre_mean":  pre_mean,
        "post_mean": post_mean,
        "pre_median": pre_median,
        "post_median": post_median,
    }
    logger.info(
        "redistribute_500k_to_1m: %d weighted filers, "
        "mean $%.0fK → $%.0fK, median $%.0fK → $%.0fK (alpha=%.2f)",
        int(total_w), pre_mean / 1e3, post_mean / 1e3,
        pre_median / 1e3, post_median / 1e3, pareto_alpha,
    )
    return out, diags
