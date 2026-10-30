"""Surgical EITC by-children reweight (lever "3a").

The Hawaii tax microsimulation builds tax units from PUMS at their natural
person/household weights — the EITC/poverty path applies no IPF rake. Aggregated
to EITC-*eligible* units, the with-children buckets fall short of the IRS SOI
Historic Table 2 Hawaii by-children counts: 1-child and 2-child eligibles come
in at ~0.74 / ~0.77 of their IRS targets, while childless and 3+ eligibles run
at or above target. A take-up truncation (pooled or the per-bucket
:func:`tax_modeler.calibration.takeup_imputation.impute_takeup_eitc_by_children`)
can only *cap* an over-produced bucket; it cannot manufacture eligibles a short
bucket lacks. The result is an EITC childless-claimer share stuck near 36-37%
against an IRS benchmark of 31.7%.

This module introduces the missing reweight. It is deliberately *surgical*: it
up-weights ONLY the EITC-eligible tax units in short qualifying-child buckets up
to their IRS target counts, with NO compensating down-weights elsewhere.
Consequences:

  * Total tax-unit weight drifts up by the amount needed to fill the 1-/2-kid
    shortfall. PUMS already over-counts (non-filing) units relative to DOTAX/SOI
    return totals, and the added weight lands on low-income EITC units that owe
    ~no income tax, so the revenue ripple is negligible.
  * SPM poverty accounting is unaffected: the SPM-unit weight is the household
    ``WGTP`` (see
    :func:`tax_modeler.poverty.spm_aggregation.aggregate_to_spm_units`),
    re-derived from the persons frame independent of the tax-unit ``weight``
    column this function edits.

Composition with the take-up step: after this reweight every bucket's eligible
weighted count is >= its IRS target, so the stratified take-up truncation then
trims each bucket to exactly its IRS share. Net: the EITC childless share lands
at 26,900 / 84,970 = 31.7%.

If the population/benchmark drift this introduces is judged unacceptable, the
heavier alternative ("3b") is to add a children x low-income margin to the
multi-margin IPF rake, which preserves the population total via compensating
down-weights and reconciles against DOTAX/SOI/filing-status — at the cost of
regenerating all calibration goldens.
"""
from __future__ import annotations

import logging

import pandas as pd

from tax_modeler.errors import DataValidationError

from .takeup_imputation import EITC_RETURNS_BY_CHILDREN_HI_TY2022

logger = logging.getLogger(__name__)


def reweight_eitc_eligibles_by_children(
    units: pd.DataFrame,
    *,
    children_col: str = "eitc_qualifying_children",
    benefit_col: str = "eitc_amount",
    weight_col: str = "weight",
    targets: dict[int, float] | None = None,
    max_factor: float | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Up-weight EITC-eligible units in short by-children buckets to IRS targets.

    For each qualifying-child bucket (0/1/2/3+, the top bucket collapsing 3+),
    the weighted count of EITC-eligible units (``benefit_col > 0``) is compared
    to its IRS target. Buckets *below* target are scaled up — every eligible
    unit in the bucket has its ``weight_col`` multiplied by
    ``target / current_weighted_count`` — so the bucket's eligible pool exactly
    reaches the IRS count. Buckets at or above target are left untouched (this is
    the surgical lever: no compensating down-weights), as are non-eligible units.

    Keyed on ``children_col`` = ``eitc_qualifying_children`` to match the
    downstream take-up truncation (:func:`impute_takeup_eitc_by_children`); the
    two compose so the post-take-up childless share equals the IRS share.

    Parameters
    ----------
    units:
        Tax-unit DataFrame *after* base-tax/EITC computation, *before* take-up.
        Must carry ``children_col``, ``benefit_col`` (eligible EITC dollars),
        and ``weight_col``.
    children_col, benefit_col, weight_col:
        Column names (defaults match the production EITC path).
    targets:
        Per-bucket IRS target counts. Defaults to
        :data:`EITC_RETURNS_BY_CHILDREN_HI_TY2022`. The top key is the "or more"
        bucket; child counts above it collapse into it.
    max_factor:
        Optional safety cap on the per-bucket up-weight factor. When a computed
        factor exceeds it, the factor is capped and a warning logged (the bucket
        then falls short of target). ``None`` (default) applies no cap.

    Returns
    -------
    tuple[pd.DataFrame, dict]
        ``(reweighted_units, info)``. ``info`` has keys ``buckets`` (a dict
        keyed by child-count, each with ``target`` / ``before`` / ``after`` /
        ``factor`` / ``n_units``), ``total_weight_before``, and
        ``total_weight_after``. Only buckets that were actually up-weighted
        appear in ``info["buckets"]``.
    """
    for col in (children_col, benefit_col, weight_col):
        if col not in units.columns:
            raise DataValidationError(
                f"reweight_eitc_eligibles_by_children requires {col!r} on units"
            )

    tgt = dict(targets if targets is not None else EITC_RETURNS_BY_CHILDREN_HI_TY2022)
    if not tgt:
        raise DataValidationError(
            "reweight_eitc_eligibles_by_children requires a non-empty targets map"
        )
    top_bucket = max(tgt)

    df = units.copy()
    n_kids = (
        df[children_col].fillna(0).clip(lower=0).astype(int).clip(upper=top_bucket)
    )
    eligible = df[benefit_col].fillna(0) > 0
    # Snapshot original weights; per-bucket masks are disjoint (distinct
    # n_kids), so scaling each from the snapshot keeps buckets independent.
    w0 = df[weight_col].fillna(0.0).astype(float)

    total_before = float(w0.sum())
    info_buckets: dict[int, dict] = {}

    for bucket in sorted(tgt):
        target = float(tgt[bucket])
        if target <= 0:
            continue
        mask = eligible & (n_kids == bucket)
        before = float(w0[mask].sum())
        if before <= 0:
            # No eligible units to scale (cannot fill a structurally empty
            # bucket by reweighting — that needs generation, not weights).
            continue
        factor = target / before
        if factor <= 1.0:
            # Bucket already at/above its IRS target; the surgical lever does
            # not down-weight over-produced buckets.
            continue
        if max_factor is not None and factor > max_factor:
            logger.warning(
                "reweight_eitc(kids=%d): factor %.2f exceeds max_factor %.2f; "
                "capping (bucket will fall short of target %.0f)",
                bucket, factor, max_factor, target,
            )
            factor = max_factor
        df.loc[mask, weight_col] = w0[mask] * factor
        after = float(df.loc[mask, weight_col].sum())
        info_buckets[bucket] = {
            "target": target,
            "before": before,
            "after": after,
            "factor": factor,
            "n_units": int(mask.sum()),
        }
        logger.info(
            "reweight_eitc(kids=%d): %d eligible units, weight %.0f -> %.0f "
            "(x%.3f toward IRS target %.0f)",
            bucket, int(mask.sum()), before, after, factor, target,
        )

    total_after = float(df[weight_col].fillna(0.0).astype(float).sum())
    drift = total_after - total_before
    logger.info(
        "reweight_eitc: total tax-unit weight %.0f -> %.0f (%+.0f, %+.2f%%)",
        total_before, total_after, drift,
        (100.0 * drift / total_before) if total_before else 0.0,
    )

    info = {
        "buckets": info_buckets,
        "total_weight_before": total_before,
        "total_weight_after": total_after,
    }
    return df, info


__all__ = ["reweight_eitc_eligibles_by_children"]
