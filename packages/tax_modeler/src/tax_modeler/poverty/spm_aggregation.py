"""Aggregate tax-unit-grained data up to SPM-unit granularity.

The poverty pipeline computes credits, taxes, and benefits at the
tax-unit level (which is correct — credits are claimed on tax returns).
For poverty *accounting*, however, Census P60-280 specifies the unit
of analysis is the SPM unit (the resource-sharing group within a
household). This module bridges the two.

Inputs:
  * ``tax_units``: tax-unit DataFrame, post-credit/benefit enrichment,
    carrying ``spm_unit_id`` (added by
    :func:`tax_modeler.units.spm_unit_assembly.attach_spm_unit_id_to_tax_units`).
  * ``persons``: PUMS persons DataFrame, post-:func:`build_spm_unit_assignment`,
    carrying ``spm_unit_id``, ``AGEP``, ``SERIALNO``, and ``PWGTP``.
    Households must also have ``WGTP`` available (either on the persons
    frame, broadcast from the household frame, or supplied via
    ``household_weight_col`` kwarg). One row per SPM unit on output
    receives the constituent household's WGTP.

Output: one row per SPM unit (filtered to non-null spm_unit_id). All
SPM resource dollar amounts are summed; geography is taken from the
representative tax unit (highest total_cash_income, deterministic
tiebreak); composition counts (``n_adults``, ``n_children``,
``n_persons``) are computed from the persons subset; ``filing_status``
is derived for the by-household-type disaggregation.
"""
from __future__ import annotations

from typing import Iterable

import numpy as np
import pandas as pd

from tax_modeler.errors import DataValidationError


# Dollar columns + count columns to sum across tax units in the same SPM
# unit. All SPM resource components plus the tax/credit columns the
# poverty pipeline reads.
_SUM_COLS: tuple[str, ...] = (
    # Income
    "income",
    "total_cash_income",
    "earned_income",
    "wage_income",
    "investment_income",
    # AGI / tax base
    "agi",
    "hi_agi",
    # Taxes (subtracted from SPM resources)
    "hi_tax_liability",
    # The food/excise credit netted into hi_tax_liability. Carried so
    # compute_spm_resources can un-net it when a dedicated
    # hi_food_excise_amount is also present (its F3 de-duplication); without
    # it the SPM-unit frame counted that credit twice.
    "hi_low_income_credit",
    "federal_tax_liability",
    "payroll_tax",
    "hi_state_tax",
    # Credits
    "eitc_amount",
    "ctc_total",
    "ctc_refundable",
    "ctc_nonrefundable",
    "hi_eitc_amount",
    "arpa_ctc_refundable",
    # Other state credits
    "hi_food_excise_amount",
    "hi_renters_amount",
    # Benefits (added to SPM resources)
    "snap_amount",
    "ssi_amount",
    "ssi_hi_amount",
    "aca_ptc_amount",
    "wic_amount",
    "housing_subsidy_amount",
    "liheap_amount",
    "childcare_amount",
    "school_lunch_amount",
    # Expenses (subtracted from SPM resources)
    "moop_amount",
    "childcare_expense_amount",
    "work_expense_amount",
    # Hypothetical programs
    "rxkids_amount",
    "rxkids_prenatal_amount",
    "rxkids_postnatal_amount",
    # Behavioral-response scenario columns (see
    # tax_modeler.scenarios.eitc_labor_response). Summed so the LFP-exit
    # resource loss aggregates correctly across multi-tax-unit SPM units.
    "lfp_behavioral_resource_loss",
    "lfp_behavioral_snap_offset",
    "intensive_resource_loss",
    # Act 163 sunset scenario: per-unit loss of the EITC and food/excise
    # expansions (forecast_working_family_credits.py).
    "act163_sunset_loss",
    # Counts
    "num_dependents",
    "num_qualifying_children",
)

# Columns taken from the representative tax unit (constant within a
# household). The aggregator asserts they are constant per SPM unit.
_REPRESENTATIVE_COLS: tuple[str, ...] = (
    "SERIALNO",
    "hh_id",
    "PUMA",
    "PUMA10",
    "tenure",
    "county",
    "house_district",
    "senate_district",
)


def _classify_spm_household_type(n_adults: int, n_children: int) -> str:
    """Map SPM unit composition to the canonical filing-status labels
    used by the by_household_type disaggregation.

    The poverty-impact report expects one of
    ``single`` / ``head_of_household`` / ``married_filing_jointly`` /
    ``married_filing_separately``. SPM units don't have a filing status,
    but the brief's HoH = "single mother" framing maps cleanly to
    ``n_adults == 1 and n_children >= 1``. Other compositions map as
    follows (chosen to keep column names stable downstream):

      * single-adult, no kids → ``single``
      * single-adult, with kids → ``head_of_household``
      * 2+ adults, with kids → ``married_filing_jointly`` (most cases;
        cohabiting partners pooled per P60-280 are functionally MFJ for
        threshold purposes)
      * 2+ adults, no kids → ``married_filing_jointly``
    """
    if n_adults <= 1 and n_children >= 1:
        return "head_of_household"
    if n_adults <= 1:
        return "single"
    return "married_filing_jointly"


# SPM-unit weight columns produced when PUMS replicate weights are present
# on the input. Census ships 80 replicate weights for variance estimation
# via Successive Difference Replication. Naming: ``weight_r01``..``weight_r80``
# mirrors the main ``weight`` column on the SPM frame.
N_REPLICATES = 80
REPLICATE_WEIGHT_COLS: tuple[str, ...] = tuple(
    f"weight_r{i:02d}" for i in range(1, N_REPLICATES + 1)
)


def aggregate_to_spm_units(
    tax_units: pd.DataFrame,
    persons: pd.DataFrame,
    *,
    spm_unit_id_col: str = "spm_unit_id",
    serialno_col: str = "SERIALNO",
    person_weight_col: str = "PWGTP",
    household_weight_col: str = "WGTP",
    agep_col: str = "AGEP",
    sum_cols: Iterable[str] | None = None,
    representative_cols: Iterable[str] | None = None,
    include_replicate_weights: bool = True,
    replicate_weight_cols: Iterable[str] | None = None,
) -> pd.DataFrame:
    """Roll tax units up to SPM units.

    Returns a DataFrame with one row per ``spm_unit_id``, ready to be
    passed to :func:`tax_modeler.poverty.spm.compute_spm_resources`
    and downstream poverty machinery.

    Tax units with null ``spm_unit_id`` (group quarters, unknown
    RELSHIPP) are dropped.

    ``replicate_weight_cols`` (e.g. ``["WGTP1", ..., "WGTP80"]``), if
    supplied, are carried onto the output per SPM unit alongside the
    full-sample ``weight`` so the result can be SDR-variance'd for ACS
    sampling uncertainty. They must already be present on ``persons``
    (broadcast from the household frame).
    """
    if spm_unit_id_col not in tax_units.columns:
        raise DataValidationError(
            f"aggregate_to_spm_units: tax_units missing {spm_unit_id_col!r}. "
            "Run attach_spm_unit_id_to_tax_units first."
        )
    if spm_unit_id_col not in persons.columns:
        raise DataValidationError(
            f"aggregate_to_spm_units: persons missing {spm_unit_id_col!r}. "
            "Run build_spm_unit_assignment first."
        )
    for col in (serialno_col, agep_col):
        if col not in persons.columns:
            raise DataValidationError(
                f"aggregate_to_spm_units: persons missing required column {col!r}"
            )

    sum_set = list(sum_cols) if sum_cols is not None else list(_SUM_COLS)
    rep_set = list(representative_cols) if representative_cols is not None else list(_REPRESENTATIVE_COLS)

    # Drop tax units with no SPM assignment (group quarters etc.)
    tu = tax_units.loc[tax_units[spm_unit_id_col].notna()].copy()
    if tu.empty:
        raise DataValidationError(
            "aggregate_to_spm_units: no tax units carry a non-null "
            "spm_unit_id; SPM-unit aggregation cannot proceed."
        )

    # Restrict persons to those assigned to an SPM unit.
    pers = persons.loc[persons[spm_unit_id_col].notna()].copy()

    # ---- 1. Sum dollar / count columns across tax units in each SPM unit
    sum_cols_present = [c for c in sum_set if c in tu.columns]
    sums = (
        tu[[spm_unit_id_col, *sum_cols_present]]
        .groupby(spm_unit_id_col, dropna=False, sort=False)
        .sum(numeric_only=True)
    )

    # ---- 2. Representative columns: pick the highest-income tax unit per
    # SPM unit (deterministic tiebreak: lowest filer_id alphabetically).
    sort_keys = []
    if "total_cash_income" in tu.columns:
        sort_keys.append("total_cash_income")
    elif "income" in tu.columns:
        sort_keys.append("income")
    if "filer_id" in tu.columns:
        sort_keys.append("filer_id")

    rep_cols_present = [c for c in rep_set if c in tu.columns]
    if sort_keys:
        rep_source = tu.sort_values(
            sort_keys, ascending=[False] + [True] * (len(sort_keys) - 1)
        ).drop_duplicates(subset=[spm_unit_id_col], keep="first")
    else:
        rep_source = tu.drop_duplicates(subset=[spm_unit_id_col], keep="first")
    reps = rep_source.set_index(spm_unit_id_col)[rep_cols_present]

    # ---- 3. Composition counts from PUMS persons
    pers_grp = pers.groupby(spm_unit_id_col, dropna=False, sort=False)
    n_persons = pers_grp.size().rename("n_persons")
    n_adults = pers_grp[agep_col].apply(lambda s: int((s >= 18).sum())).rename("n_adults")
    n_children = pers_grp[agep_col].apply(lambda s: int((s < 18).sum())).rename("n_children")
    composition = pd.concat([n_persons, n_adults, n_children], axis=1)

    # ---- 4. Weight: raw household WGTP, one value per SERIALNO.
    if household_weight_col in pers.columns:
        # All persons in a household share the same WGTP; pick one per
        # SPM unit (they're constant within a household, and SPM units
        # never cross households).
        wgtp_per_spm = (
            pers.groupby(spm_unit_id_col, dropna=False, sort=False)[household_weight_col]
            .first()
            .rename("weight")
        )
        replicate_source_frame = pers
    elif household_weight_col in tu.columns:
        wgtp_per_spm = (
            tu.groupby(spm_unit_id_col, dropna=False, sort=False)[household_weight_col]
            .first()
            .rename("weight")
        )
        replicate_source_frame = tu
    elif "hh_weight" in tu.columns:
        wgtp_per_spm = (
            tu.groupby(spm_unit_id_col, dropna=False, sort=False)["hh_weight"]
            .first()
            .rename("weight")
        )
        replicate_source_frame = tu
    else:
        raise DataValidationError(
            f"aggregate_to_spm_units: cannot find a household weight column "
            f"({household_weight_col!r} on persons or tax_units; 'hh_weight' "
            "on tax_units). One of these is required for the SPM-unit weight."
        )

    # ---- 4b. Replicate weights (optional). Census ships 80 PUMS replicate
    # weights as WGTP1..WGTP80 for variance estimation via SDR. If they are
    # present on the source frame, roll them up identically to the main
    # WGTP — first value per SPM unit (they are household-constant).
    #
    # Two emission modes coexist:
    #   * ``include_replicate_weights`` (default): auto-detect WGTP1..80 on the
    #     source frame and emit canonical ``weight_r01..weight_r80`` — consumed
    #     by impact._detect_replicate_weight_cols / hi_eitc_takeup_estimate.
    #   * ``replicate_weight_cols``: explicit column list emitted verbatim
    #     (names preserved) for tax_modeler.poverty.uncertainty SDR re-aggregation.
    # Column names never collide (weight_r## vs WGTP#), so both may run.
    replicate_per_spm: dict[str, pd.Series] = {}
    if include_replicate_weights:
        for r in range(1, N_REPLICATES + 1):
            src_col = f"{household_weight_col}{r}"
            if src_col in replicate_source_frame.columns:
                out_col = f"weight_r{r:02d}"
                replicate_per_spm[out_col] = (
                    replicate_source_frame.groupby(
                        spm_unit_id_col, dropna=False, sort=False
                    )[src_col]
                    .first()
                    .rename(out_col)
                )

    rep_wt_set = list(replicate_weight_cols) if replicate_weight_cols is not None else []
    rep_weights = None
    if rep_wt_set:
        missing = [c for c in rep_wt_set if c not in pers.columns]
        if missing:
            raise DataValidationError(
                "aggregate_to_spm_units: replicate_weight_cols not found on "
                f"persons: {missing[:5]}. Broadcast them from households first."
            )
        rep_weights = (
            pers.groupby(spm_unit_id_col, dropna=False, sort=False)[rep_wt_set]
            .first()
        )

    # ---- 5. Audit columns
    n_tax_units = (
        tu.groupby(spm_unit_id_col, dropna=False, sort=False).size().rename("n_tax_units_pooled")
    )

    # ---- 6. Stitch together
    out = (
        sums.join(reps, how="left")
        .join(composition, how="left")
        .join(wgtp_per_spm, how="left")
    )
    if replicate_per_spm:
        rep_df = pd.concat(replicate_per_spm.values(), axis=1)
        out = out.join(rep_df, how="left")
    if rep_weights is not None:
        out = out.join(rep_weights, how="left")
    out = out.join(n_tax_units, how="left").reset_index()

    # ---- 7. Derive spm-unit filing status proxy (for by_household_type)
    out["filing_status"] = [
        _classify_spm_household_type(int(a), int(c))
        for a, c in zip(out["n_adults"].fillna(0), out["n_children"].fillna(0))
    ]
    out["is_multi_tax_unit_spm"] = out["n_tax_units_pooled"] > 1

    # ---- 8. Defensive checks: PUMA constant within SERIALNO
    if "PUMA" in out.columns and serialno_col in out.columns:
        puma_unique = out.groupby(serialno_col)["PUMA"].nunique()
        if (puma_unique > 1).any():
            offending = puma_unique[puma_unique > 1].index.tolist()
            raise DataValidationError(
                f"aggregate_to_spm_units: PUMA is not constant within "
                f"SERIALNO for households {offending[:5]} (and possibly more). "
                "PUMS households should be single-PUMA — investigate the loader."
            )

    return out


__all__ = [
    "aggregate_to_spm_units",
    "N_REPLICATES",
    "REPLICATE_WEIGHT_COLS",
]
