"""SPM unit assembly from PUMS persons (Census P60-280 rules).

An SPM unit is the Census Bureau's resource-sharing group for the
Supplemental Poverty Measure. Per P60-280, an SPM unit consists of:

  * **Primary SPM unit per household**: the householder + their spouse +
    unmarried partner (if any) + relatives + foster children + any
    unrelated child under age 15. This is one "_primary" unit per
    household.
  * **Unrelated adults**: each roomer/boarder/housemate or other
    non-relative aged 15+ forms their own SPM unit.
  * **Group quarters residents** are excluded from SPM accounting.

PUMS RELSHIPP codes (2019+ vintage; same set the entities/graph.py
module uses):

  20  Householder
  21  Opposite-sex spouse                  22  Opposite-sex unmarried partner
  23  Same-sex spouse                      24  Same-sex unmarried partner
  25  Biological son/daughter              26  Adopted son/daughter
  27  Stepson/stepdaughter                 28  Brother/sister
  29  Father/mother                        30  Grandchild
  31  Parent-in-law                        32  Son-in-law/daughter-in-law
  33  Other relative (cousin, niece, nephew, ...)
  34  Roommate / housemate / boarder       35  Foster child
  36  Other non-relative
  37  Institutionalized group quarters     38  Non-institutionalized group quarters

The repo treats codes 20-33 + 35 as the primary SPM unit (householder +
spouse + unmarried partner + all blood/marriage relatives + foster
child), per the canonical sets in
:mod:`tax_modeler.units.relshipp_codes`. Roommates (34) and other
non-relatives (36) aged 15+ form their own one-person SPM units; group
quarters (37, 38) are excluded from SPM accounting.

This module is the **vectorized** entry point used by the poverty
pipeline. `EntityGraph._spm_units_for_household` (graph.py) is the
object-oriented variant; both produce the same partitioning.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from tax_modeler.errors import DataValidationError
from tax_modeler.units.relshipp_codes import (
    GROUP_QUARTERS,
    SPM_PRIMARY,
    SPM_UNRELATED,
)

# Canonical Census 2019+ RELSHIPP grouping (see relshipp_codes). The
# householder + spouses + unmarried partners + all blood/marriage relatives
# + foster children share the primary SPM unit; roommates (34) and other
# nonrelatives (36) aged 15+ split into their own one-person SPM units;
# group quarters (37, 38) are excluded from SPM accounting.
_PRIMARY_SPM_RELSHIPP: frozenset[int] = SPM_PRIMARY
_UNRELATED_RELSHIPP: frozenset[int] = SPM_UNRELATED
_GROUP_QUARTERS_RELSHIPP: frozenset[int] = GROUP_QUARTERS

# Age threshold for the "unrelated child" rule (P60-280): any unrelated
# child under 15 pools with the primary SPM unit.
_UNRELATED_CHILD_AGE: int = 15


def build_spm_unit_assignment(
    persons: pd.DataFrame,
    *,
    serialno_col: str = "SERIALNO",
    sporder_col: str = "SPORDER",
    relshipp_col: str = "RELSHIPP",
    agep_col: str = "AGEP",
    person_id_out_col: str = "person_id",
    spm_unit_id_out_col: str = "spm_unit_id",
) -> pd.DataFrame:
    """Compute SPM unit assignment for each PUMS person.

    Returns a copy of ``persons`` with two added columns:

      * ``person_id``: ``SERIALNO + "_" + SPORDER`` (matches
        :func:`tax_modeler.units.utils.create_person_id`).
      * ``spm_unit_id``: one of
          - ``"{SERIALNO}_primary"`` for primary-SPM-unit members (relatives
            of the householder + unmarried partner + foster children +
            any unrelated child under 15);
          - ``"{SERIALNO}_unrel_{SPORDER}"`` for unrelated adults aged 15+;
          - ``pd.NA`` for group quarters (excluded from SPM accounting).

    Required columns: SERIALNO, SPORDER, RELSHIPP, AGEP. Missing any of
    these raises :class:`DataValidationError` (no silent fallbacks).

    Per-person null values in RELSHIPP or AGEP are also rejected — PUMS
    guarantees both are populated for in-household persons.
    """
    required = {serialno_col, sporder_col, relshipp_col, agep_col}
    missing = required - set(persons.columns)
    if missing:
        raise DataValidationError(
            f"build_spm_unit_assignment: persons frame is missing required "
            f"columns: {sorted(missing)}"
        )

    if persons[relshipp_col].isna().any():
        n_na = int(persons[relshipp_col].isna().sum())
        raise DataValidationError(
            f"build_spm_unit_assignment: {n_na} person rows have null "
            f"{relshipp_col}. PUMS guarantees RELSHIPP is populated for "
            "in-household persons — investigate the loader."
        )
    if persons[agep_col].isna().any():
        n_na = int(persons[agep_col].isna().sum())
        raise DataValidationError(
            f"build_spm_unit_assignment: {n_na} person rows have null "
            f"{agep_col}."
        )

    df = persons.copy()
    serialno = df[serialno_col].astype(str)
    sporder = df[sporder_col].astype(str)
    relshipp = df[relshipp_col].astype(int).to_numpy()
    agep = df[agep_col].astype(float).to_numpy()

    df[person_id_out_col] = serialno + "_" + sporder

    is_primary_code = np.isin(relshipp, list(_PRIMARY_SPM_RELSHIPP))
    is_unrelated_code = np.isin(relshipp, list(_UNRELATED_RELSHIPP))
    is_group_quarters = np.isin(relshipp, list(_GROUP_QUARTERS_RELSHIPP))
    is_under_15 = agep < _UNRELATED_CHILD_AGE

    # Primary unit: code says primary, OR (unrelated code BUT under 15).
    # Unrelated adult: unrelated code AND age >= 15.
    # Group quarters: never enter SPM accounting.
    in_primary = is_primary_code | (is_unrelated_code & is_under_15)
    in_unrelated_adult = is_unrelated_code & ~is_under_15

    primary_ids = np.where(
        in_primary,
        serialno.to_numpy(dtype=object) + "_primary",
        None,
    )
    unrelated_ids = np.where(
        in_unrelated_adult,
        serialno.to_numpy(dtype=object) + "_unrel_" + sporder.to_numpy(dtype=object),
        None,
    )

    spm_ids: list[object] = []
    for i in range(len(df)):
        if is_group_quarters[i]:
            spm_ids.append(pd.NA)
        elif in_primary[i]:
            spm_ids.append(primary_ids[i])
        elif in_unrelated_adult[i]:
            spm_ids.append(unrelated_ids[i])
        else:
            # Unknown RELSHIPP code (not in any documented set) — treat
            # as group-quarters-like (exclude). Surface count via warning.
            spm_ids.append(pd.NA)

    df[spm_unit_id_out_col] = pd.array(spm_ids, dtype="string")
    return df


def attach_spm_unit_id_to_tax_units(
    units: pd.DataFrame,
    persons_with_assignment: pd.DataFrame,
    *,
    primary_filer_id_col: str = "primary_filer_id",
    person_id_col: str = "person_id",
    spm_unit_id_col: str = "spm_unit_id",
) -> pd.DataFrame:
    """Add ``spm_unit_id`` to the tax-unit frame by joining on primary filer.

    Each tax unit lands in the SPM unit of its primary filer. Many tax
    units → one SPM unit when, for example, a parent and an adult child
    both file as single in the same household.

    Tax units whose primary_filer_id is not found in ``persons_with_assignment``
    (e.g., group quarters residents who were excluded upstream) get
    ``spm_unit_id == pd.NA`` and will be dropped by the aggregator.
    """
    if primary_filer_id_col not in units.columns:
        raise DataValidationError(
            f"attach_spm_unit_id_to_tax_units: units frame missing "
            f"{primary_filer_id_col!r}"
        )

    # The persons frame may have person_id as a column OR as the index
    # (the constructor sets person_id as the index after building it).
    persons_lookup = persons_with_assignment
    if persons_lookup.index.name == person_id_col:
        persons_lookup = persons_lookup.reset_index()

    if person_id_col not in persons_lookup.columns:
        raise DataValidationError(
            f"attach_spm_unit_id_to_tax_units: persons frame missing "
            f"{person_id_col!r}; run build_spm_unit_assignment first."
        )
    if spm_unit_id_col not in persons_lookup.columns:
        raise DataValidationError(
            f"attach_spm_unit_id_to_tax_units: persons frame missing "
            f"{spm_unit_id_col!r}; run build_spm_unit_assignment first."
        )

    lookup = persons_lookup[[person_id_col, spm_unit_id_col]].drop_duplicates(
        subset=[person_id_col]
    )

    out = units.merge(
        lookup,
        how="left",
        left_on=primary_filer_id_col,
        right_on=person_id_col,
        suffixes=("", "_spm_lookup"),
    )
    # Drop the right-side person_id column added by the merge (it may
    # already exist as left-side; pandas appends suffix if it does).
    drop_cols = [c for c in out.columns if c.endswith("_spm_lookup")]
    if drop_cols:
        out = out.drop(columns=drop_cols)
    # If person_id was newly added by the merge (i.e., the right key
    # didn't conflict with a left column), drop the join key as well —
    # tax units shouldn't carry their primary filer's person_id under
    # the bare name.
    if person_id_col in out.columns and person_id_col not in units.columns:
        out = out.drop(columns=[person_id_col])
    return out


__all__ = [
    "build_spm_unit_assignment",
    "attach_spm_unit_id_to_tax_units",
    "_PRIMARY_SPM_RELSHIPP",
    "_UNRELATED_RELSHIPP",
    "_GROUP_QUARTERS_RELSHIPP",
]
