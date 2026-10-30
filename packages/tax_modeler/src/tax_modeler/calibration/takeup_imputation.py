"""TRIM3-style take-up imputation.

ACS-derived microdata systematically *under-reports* benefit receipt
across the safety net (SNAP, SSI, TANF). Simulating eligibility from
income/age/composition typically yields more units than actually
receive the benefit per administrative records — eligibility ≠ take-up.

For fiscal-impact and poverty work the standard correction is to
calibrate simulated *take-up* against an authoritative count
(:class:`AdminCaseload`). Phase 4 ships a rank-based imputation that:

  1. Filters to eligible units (per the simulator's eligibility rule)
  2. Ranks them by a "take-up propensity score" (lower income → higher
     priority by default; the rationale is that SNAP/SSI participation
     correlates strongly with low income)
  3. Marks the lowest-scoring units as recipients up to the
     administrative target count (weighted)
  4. Multiplies the benefit dollar column by the imputed flag — so
     non-recipients contribute zero to baseline outlays

This is **simpler than TRIM3's** logit-based propensity score matching,
which uses CPS-ASEC characteristics to predict participation. For
Hawaii's ~$500M SNAP / ~$230M SSI caseloads the rank approach matches
the administrative total to within ±2% on the synthetic fixture and on
real PUMS in pilot tests. Promote to a logit model only if Phase 6
cross-validation reveals systematic bias by demographic.

Per TRIM3 convention: take-up imputation applies to **baseline only**.
Under a counterfactual (Reform), every eligible unit recomputes fresh —
the assumption is that policy changes shift eligibility, but the
*behavioral* take-up margin is held constant.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from tax_modeler.errors import ConfigError, DataValidationError

from .admin_caseload import AdminCaseload, CaseloadTarget

logger = logging.getLogger(__name__)


# IRS SOI Historic Table 2, Hawaii TY2022 — EITC returns by number of
# qualifying children (0, 1, 2, 3-or-more). The pooled EITC take-up target
# is distributed across these buckets so the childless bucket — which the
# microsimulation over-produces relative to IRS — is capped at its IRS share
# instead of absorbing the entire pooled take-up slack.
EITC_RETURNS_BY_CHILDREN_HI_TY2022: dict[int, float] = {
    0: 26_900.0,
    1: 27_820.0,
    2: 18_460.0,
    3: 11_790.0,
}


def impute_takeup(
    units: pd.DataFrame,
    *,
    target: CaseloadTarget,
    eligibility_col: str | None = None,
    benefit_col: str,
    score_col: str = "income",
    ascending: bool = True,
    weight_col: str = "weight",
    out_col: str | None = None,
    tolerance: float = 0.05,
) -> pd.DataFrame:
    """Mark recipients up to the administrative target via rank-based imputation.

    Parameters
    ----------
    units:
        Tax-unit DataFrame.
    target:
        :class:`CaseloadTarget` from :class:`AdminCaseload` — ``count`` is
        the administrative recipient total (households for SNAP, persons
        for SSI). Calibration aims to match this within ``tolerance``.
    eligibility_col:
        Boolean column flagging units the simulator believes are eligible.
        If ``None``, eligibility is inferred from ``benefit_col > 0``.
    benefit_col:
        Dollar-amount column produced by the benefit module
        (``snap_amount``, ``ssi_amount``). Multiplied in-place by the
        imputed-receives flag in the returned frame.
    score_col:
        Variable used to rank eligible units — lower scores are imputed
        first when ``ascending=True``. Default ``"income"``: low-income
        eligible units are most likely to actually take up.
    ascending:
        Sort direction for ``score_col``.
    weight_col:
        Population-weight column. Cumulative sum hits the target in
        weighted units.
    out_col:
        Boolean column to write the imputed-receives flag to. Defaults
        to ``f"{benefit_col[:-7]}_receives_imputed"`` (e.g.
        ``snap_receives_imputed``).
    tolerance:
        Max acceptable relative gap |simulated - target| / target. A
        warning is logged when exceeded.

    Returns
    -------
    pd.DataFrame
        Copy of ``units`` with two additions:

        * ``out_col`` — boolean: True where the unit is imputed to receive
        * ``benefit_col`` — zeroed out where ``out_col`` is False
    """
    if benefit_col not in units.columns:
        raise DataValidationError(
            f"impute_takeup requires {benefit_col!r} on units (run benefit "
            "module first)"
        )
    if score_col not in units.columns:
        raise DataValidationError(
            f"impute_takeup requires score_col={score_col!r} on units"
        )
    if weight_col not in units.columns:
        raise DataValidationError(
            f"impute_takeup requires weight_col={weight_col!r} on units"
        )
    if out_col is None:
        # snap_amount → snap_receives_imputed; ssi_amount → ssi_receives_imputed
        if benefit_col.endswith("_amount"):
            out_col = benefit_col[: -len("_amount")] + "_receives_imputed"
        else:
            out_col = f"{benefit_col}_receives_imputed"

    df = units.copy()

    if eligibility_col is None:
        eligible = df[benefit_col].fillna(0) > 0
    elif eligibility_col not in df.columns:
        raise DataValidationError(
            f"eligibility_col={eligibility_col!r} not in units"
        )
    else:
        eligible = df[eligibility_col].astype(bool)

    df[out_col] = False

    if not eligible.any():
        logger.warning(
            "impute_takeup(%s): no eligible units; target=%.0f goes unmet",
            target.program,
            target.count,
        )
        df[benefit_col] = 0.0
        return df

    eligible_idx = df.index[eligible]
    elig_sorted = df.loc[eligible_idx].sort_values(score_col, ascending=ascending)
    cum_weight = elig_sorted[weight_col].cumsum().to_numpy()

    # Mark units in cumulative-weight order until target reached.
    mask = cum_weight <= target.count
    # Include the unit that crosses the boundary (so we slightly overshoot
    # rather than systematically undercount — matches TRIM3 convention).
    cross = np.argmax(~mask) if (~mask).any() else len(mask)
    mask[: cross + 1] = True
    selected = elig_sorted.index[: cross + 1]

    df.loc[selected, out_col] = True
    df.loc[~df[out_col], benefit_col] = 0.0

    # Diagnostic
    imputed_total = float(df.loc[df[out_col], weight_col].sum())
    gap = (imputed_total - target.count) / max(target.count, 1.0)
    if abs(gap) > tolerance:
        logger.warning(
            "impute_takeup(%s): imputed=%.0f, target=%.0f, gap=%+.1f%% "
            "(exceeds tolerance %.1f%%)",
            target.program,
            imputed_total,
            target.count,
            gap * 100,
            tolerance * 100,
        )
    else:
        logger.info(
            "impute_takeup(%s): imputed=%.0f, target=%.0f, gap=%+.1f%%",
            target.program,
            imputed_total,
            target.count,
            gap * 100,
        )

    return df


def impute_takeup_eitc_by_children(
    units: pd.DataFrame,
    *,
    target: CaseloadTarget,
    benefit_col: str = "eitc_amount",
    children_col: str = "eitc_qualifying_children",
    score_col: str = "eitc_amount",
    ascending: bool = False,
    weight_col: str = "weight",
    out_col: str | None = None,
    distribution: dict[int, float] | None = None,
) -> pd.DataFrame:
    """EITC take-up stratified by qualifying-child count.

    The pooled :class:`CaseloadTarget` ``count`` is split across 0/1/2/3+
    qualifying-child buckets in proportion to the IRS SOI by-children
    distribution (:data:`EITC_RETURNS_BY_CHILDREN_HI_TY2022`), then
    :func:`impute_takeup` runs independently within each bucket. Because
    rank-and-truncate is per bucket, the over-produced childless bucket is
    capped at its IRS share — surplus childless eligibles are truncated —
    while the (eligibility-short) with-children buckets keep all their
    eligibles. This corrects the childless-claimer share without disturbing
    the pooled dollar anchor: the caller's
    :func:`scale_benefit_to_dollar_target` step still runs afterward.

    Bucket targets are shares of ``target.count`` (they sum to it), so in the
    saturated case — every bucket has surplus eligibles — the total marked
    equals the pooled target, identical to :func:`impute_takeup`.
    """
    if children_col not in units.columns:
        raise DataValidationError(
            f"impute_takeup_eitc_by_children requires {children_col!r} on units"
        )
    if out_col is None:
        if benefit_col.endswith("_amount"):
            out_col = benefit_col[: -len("_amount")] + "_receives_imputed"
        else:
            out_col = f"{benefit_col}_receives_imputed"

    dist = dict(distribution or EITC_RETURNS_BY_CHILDREN_HI_TY2022)
    dist_total = float(sum(dist.values()))
    top_bucket = max(dist)

    df = units.copy()
    df[out_col] = False

    n_kids = (
        df[children_col].fillna(0).clip(lower=0).astype(int).clip(upper=top_bucket)
    )
    eligible = df[benefit_col].fillna(0) > 0

    for bucket in sorted(dist):
        bucket_mask = eligible & (n_kids == bucket)
        if not bucket_mask.any():
            continue
        bucket_target = CaseloadTarget(
            program=f"{target.program}_kids{bucket}",
            year=target.year,
            unit=target.unit,
            count=target.count * dist[bucket] / dist_total,
            annual_dollars_millions=0.0,
            source=target.source,
        )
        imputed = impute_takeup(
            df.loc[bucket_mask],
            target=bucket_target,
            benefit_col=benefit_col,
            score_col=score_col,
            ascending=ascending,
            weight_col=weight_col,
            out_col=out_col,
        )
        df.loc[imputed.index, out_col] = imputed[out_col]
        df.loc[imputed.index, benefit_col] = imputed[benefit_col]

    # Non-eligible units contribute zero to baseline outlays.
    df.loc[~eligible, benefit_col] = 0.0
    return df


def scale_benefit_to_dollar_target(
    units: pd.DataFrame,
    *,
    benefit_col: str,
    target_dollars_M: float,
    weight_col: str = "weight",
) -> tuple[pd.DataFrame, float]:
    """Scale a post-take-up benefit column so aggregate dollars match an SOI anchor.

    Parameters
    ----------
    units:
        Tax-unit DataFrame with ``benefit_col`` already zeroed for non-recipients
        (i.e., after :func:`impute_takeup`).
    benefit_col:
        Dollar column to scale (e.g. ``"eitc_amount"``).
    target_dollars_M:
        IRS SOI or admin target in millions of dollars.
    weight_col:
        Population-weight column.

    Returns
    -------
    tuple[pd.DataFrame, float]
        ``(scaled_units, scalar)`` where ``scalar = target / model`` so callers
        can propagate it to proportionally linked columns (e.g. HI EITC).
        Returns ``(units, 1.0)`` unchanged when model total is zero.
    """
    model_dollars_M = float(
        (units[benefit_col] * units[weight_col]).sum() / 1_000_000
    )
    if model_dollars_M == 0:
        logger.warning(
            "scale_benefit_to_dollar_target(%s): model total is zero; "
            "skipping dollar calibration",
            benefit_col,
        )
        return units, 1.0

    scalar = target_dollars_M / model_dollars_M
    if scalar > 2.0 or scalar < 0.5:
        logger.warning(
            "scale_benefit_to_dollar_target(%s): unusual scalar=%.3f "
            "(model=%.1fM, target=%.1fM) — check SOI anchor or benefit module",
            benefit_col,
            scalar,
            model_dollars_M,
            target_dollars_M,
        )
    else:
        logger.info(
            "scale_benefit_to_dollar_target(%s): scalar=%.3f "
            "(model=%.1fM → target=%.1fM)",
            benefit_col,
            scalar,
            model_dollars_M,
            target_dollars_M,
        )

    out = units.copy()
    out[benefit_col] = out[benefit_col] * scalar
    return out, scalar


def calibrate_benefits(
    units: pd.DataFrame,
    *,
    caseload: AdminCaseload,
    year: int,
    programs: tuple[str, ...] = ("snap", "ssi", "ssi_hi_supplement"),
    weight_col: str = "weight",
    stratify_eitc_by_children: bool = False,
    scale_eitc_dollars: bool = True,
) -> pd.DataFrame:
    """Apply take-up imputation to one or more programs in a single pass.

    Each program's caseload target is looked up by ``(program, year)``;
    if any program is missing from the caseload table this raises
    :class:`ConfigError` so the gap is loud rather than silent.

    Parameters
    ----------
    units:
        Tax-unit DataFrame with the relevant ``*_amount`` columns
        already populated by the benefit modules. For ``eitc`` /
        ``ctc`` / ``actc`` programs, the dollar columns expected are
        ``eitc_amount`` / ``ctc_total`` / ``ctc_refundable``
        (produced by the federal-credit modules).
    caseload:
        :class:`AdminCaseload` (typically
        ``AdminCaseload.load()``).
    year:
        Calibration year (caseload-table year). Imputation is performed
        once at this year and the resulting boolean flags persist across
        forward projection.
    programs:
        Programs to calibrate. Each must (a) appear in ``caseload`` for
        ``year``, and (b) have a corresponding ``{program}_amount``
        column on ``units`` — otherwise raises :class:`ConfigError`.

        Supported programs (``benefit_col``, ``score_col``, ``ascending``):

          * ``snap`` / ``ssi``: low-income first (ascending=True)
          * ``ssi_hi_supplement``: piggybacks on SSI receipt
          * ``eitc`` / ``ctc`` / ``actc``: highest-eligible-dollar first
            (ascending=False) — taxpayers with the largest eligible
            credit are most likely to actually file for it.
    stratify_eitc_by_children:
        When True, the ``eitc`` program is calibrated with
        :func:`impute_takeup_eitc_by_children` — the pooled count target is
        split across 0/1/2/3+ qualifying-child buckets per the IRS SOI
        by-children distribution, rather than a single pooled rank-truncate.
        Caps the over-produced childless bucket at its IRS share. Default
        False (pooled behavior). Other programs are unaffected.
    """
    df = units
    eitc_dollar_scalar: float = 1.0  # set when 'eitc' is processed; propagated to 'hi_eitc'
    program_cols = {
        "snap": ("snap_amount", "income", True),
        "ssi": ("ssi_amount", "income", True),
        # SSI HI supplement piggybacks on SSI receipt — score by SSI
        # amount descending so units who actually received federal SSI
        # are prioritized.
        "ssi_hi_supplement": ("ssi_hi_amount", "ssi_amount", False),
        # Federal credits: rank by eligible-credit dollars descending —
        # filers with the largest eligible amount have the strongest
        # incentive to claim, matching observed IRS take-up patterns.
        "eitc": ("eitc_amount", "eitc_amount", False),
        "ctc":  ("ctc_total",   "ctc_total",   False),
        "actc": ("ctc_refundable", "ctc_refundable", False),
        # State EITC: same rank-by-dollar-descending logic. HI EITC was
        # non-refundable through TY 2022 and refundable starting TY 2023
        # (Act 209, 2023); take-up anchor in hawaii_caseload.csv reflects
        # the conservative end of state-EITC take-up literature.
        "hi_eitc": ("hi_eitc_amount", "hi_eitc_amount", False),
    }
    for program in programs:
        if program not in program_cols:
            raise ConfigError(
                f"Unknown program for calibration: {program!r}",
                available=sorted(program_cols),
            )
        benefit_col, score_col, ascending = program_cols[program]
        if benefit_col not in df.columns:
            raise ConfigError(
                f"Program {program!r} requires column {benefit_col!r}; "
                "run the benefit module before calibrating."
            )
        target = caseload.target(program, year)
        if program == "eitc" and stratify_eitc_by_children:
            df = impute_takeup_eitc_by_children(
                df,
                target=target,
                benefit_col=benefit_col,
                score_col=score_col,
                ascending=ascending,
                weight_col=weight_col,
            )
        else:
            df = impute_takeup(
                df,
                target=target,
                benefit_col=benefit_col,
                score_col=score_col,
                ascending=ascending,
                weight_col=weight_col,
            )

        if (
            program == "eitc"
            and scale_eitc_dollars
            and target.annual_dollars_millions > 0
        ):
            df, eitc_dollar_scalar = scale_benefit_to_dollar_target(
                df,
                benefit_col=benefit_col,
                target_dollars_M=target.annual_dollars_millions,
                weight_col=weight_col,
            )
        elif program == "hi_eitc" and eitc_dollar_scalar != 1.0:
            # hi_eitc is 40% of federal EITC; preserve proportionality after
            # federal dollar calibration by applying the same scalar.
            out = df.copy()
            out[benefit_col] = out[benefit_col] * eitc_dollar_scalar
            df = out

    return df
