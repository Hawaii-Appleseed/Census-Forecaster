"""Tests for SPM unit assembly from PUMS persons (Census P60-280 rules)."""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.errors import DataValidationError
from tax_modeler.units.spm_unit_assembly import (
    attach_spm_unit_id_to_tax_units,
    build_spm_unit_assignment,
)


def _make_persons(rows: list[dict]) -> pd.DataFrame:
    """Helper: build a persons DataFrame from a list of dicts."""
    return pd.DataFrame(rows)


def test_primary_spm_unit_covers_householder_and_relatives():
    """Householder (20) + spouse (21) + child (25) + parent (29) +
    parent-in-law (31) all pool into the one primary SPM unit. All five
    canonical codes are in SPM_PRIMARY."""
    persons = _make_persons([
        {"SERIALNO": "S1", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 45},  # head
        {"SERIALNO": "S1", "SPORDER": 2, "RELSHIPP": 21, "AGEP": 42},  # spouse
        {"SERIALNO": "S1", "SPORDER": 3, "RELSHIPP": 25, "AGEP": 10},  # child
        {"SERIALNO": "S1", "SPORDER": 4, "RELSHIPP": 29, "AGEP": 70},  # parent
        {"SERIALNO": "S1", "SPORDER": 5, "RELSHIPP": 31, "AGEP": 68},  # parent-in-law
    ])
    out = build_spm_unit_assignment(persons)
    assert set(out["spm_unit_id"]) == {"S1_primary"}


def test_cohabiting_partner_with_shared_kid_in_primary_spm_unit():
    """Unmarried partner (RELSHIPP 22) + their child (RELSHIPP 25) +
    foster child (RELSHIPP 35) pool with the householder's primary SPM
    unit."""
    persons = _make_persons([
        {"SERIALNO": "S2", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 35},
        {"SERIALNO": "S2", "SPORDER": 2, "RELSHIPP": 22, "AGEP": 33},  # partner
        {"SERIALNO": "S2", "SPORDER": 3, "RELSHIPP": 25, "AGEP": 5},   # shared child
        {"SERIALNO": "S2", "SPORDER": 4, "RELSHIPP": 35, "AGEP": 8},   # foster
    ])
    out = build_spm_unit_assignment(persons)
    assert set(out["spm_unit_id"]) == {"S2_primary"}


def test_unrelated_adult_roommate_gets_own_spm_unit():
    """Unrelated adult (RELSHIPP 34 = roommate) aged 15+ → separate SPM unit."""
    persons = _make_persons([
        {"SERIALNO": "S3", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 30},
        {"SERIALNO": "S3", "SPORDER": 2, "RELSHIPP": 34, "AGEP": 28},  # roommate
    ])
    out = build_spm_unit_assignment(persons)
    ids = list(out["spm_unit_id"])
    assert ids[0] == "S3_primary"
    assert ids[1] == "S3_unrel_2"


def test_under_15_unrelated_child_pools_with_primary():
    """Per P60-280, unrelated child <15 joins the householder's SPM unit."""
    persons = _make_persons([
        {"SERIALNO": "S4", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 45},
        {"SERIALNO": "S4", "SPORDER": 2, "RELSHIPP": 34, "AGEP": 8},   # unrelated child <15
    ])
    out = build_spm_unit_assignment(persons)
    assert set(out["spm_unit_id"]) == {"S4_primary"}


def test_group_quarters_excluded():
    """RELSHIPP 37/38 (institutional / non-institutional GQ) → spm_unit_id NA."""
    persons = _make_persons([
        {"SERIALNO": "GQ1", "SPORDER": 1, "RELSHIPP": 37, "AGEP": 25},
        {"SERIALNO": "GQ2", "SPORDER": 1, "RELSHIPP": 38, "AGEP": 22},
    ])
    out = build_spm_unit_assignment(persons)
    assert out["spm_unit_id"].isna().all()


def test_missing_relshipp_raises():
    """Missing RELSHIPP must fail loudly (no silent fallback)."""
    persons = _make_persons([
        {"SERIALNO": "S5", "SPORDER": 1, "RELSHIPP": pd.NA, "AGEP": 30},
    ])
    with pytest.raises(DataValidationError, match="RELSHIPP"):
        build_spm_unit_assignment(persons)


def test_missing_agep_raises():
    """Missing AGEP also fails loudly."""
    persons = _make_persons([
        {"SERIALNO": "S6", "SPORDER": 1, "RELSHIPP": 20, "AGEP": pd.NA},
    ])
    with pytest.raises(DataValidationError, match="AGEP"):
        build_spm_unit_assignment(persons)


def test_missing_required_column_raises():
    persons = pd.DataFrame({"SERIALNO": ["S7"], "SPORDER": [1], "RELSHIPP": [20]})
    with pytest.raises(DataValidationError, match="AGEP"):
        build_spm_unit_assignment(persons)


def test_person_id_format_matches_create_person_id():
    """The output person_id must match utils.create_person_id format
    (SERIALNO + '_' + SPORDER, both stringified)."""
    persons = _make_persons([
        {"SERIALNO": "ABC123", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 40},
        {"SERIALNO": "ABC123", "SPORDER": 2, "RELSHIPP": 25, "AGEP": 12},
    ])
    out = build_spm_unit_assignment(persons)
    assert list(out["person_id"]) == ["ABC123_1", "ABC123_2"]


def test_attach_to_tax_units_joins_by_primary_filer_id():
    """Tax units pick up the SPM unit of their primary filer."""
    persons = _make_persons([
        {"SERIALNO": "H1", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 50},
        {"SERIALNO": "H1", "SPORDER": 2, "RELSHIPP": 25, "AGEP": 25},  # adult child
        {"SERIALNO": "H1", "SPORDER": 3, "RELSHIPP": 25, "AGEP": 12},  # younger child
    ])
    assigned = build_spm_unit_assignment(persons)
    # Two tax units sharing one household: parent files single, adult child files single
    units = pd.DataFrame([
        {"filer_id": "tu_parent", "primary_filer_id": "H1_1", "filing_status": "head_of_household"},
        {"filer_id": "tu_child",  "primary_filer_id": "H1_2", "filing_status": "single"},
    ])
    out = attach_spm_unit_id_to_tax_units(units, assigned)
    # Both tax units must land in the same primary SPM unit (since the adult
    # child is RELSHIPP 25 = related to the householder).
    assert list(out["spm_unit_id"]) == ["H1_primary", "H1_primary"]


def test_attach_unrelated_adult_gets_own_spm_unit_id():
    """Tax unit headed by an unrelated adult (RELSHIPP 34, age 28) lands
    in the unrel SPM unit, not the household's primary."""
    persons = _make_persons([
        {"SERIALNO": "H2", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 40},
        {"SERIALNO": "H2", "SPORDER": 2, "RELSHIPP": 34, "AGEP": 28},  # roommate
    ])
    assigned = build_spm_unit_assignment(persons)
    units = pd.DataFrame([
        {"filer_id": "tu_head", "primary_filer_id": "H2_1", "filing_status": "single"},
        {"filer_id": "tu_room", "primary_filer_id": "H2_2", "filing_status": "single"},
    ])
    out = attach_spm_unit_id_to_tax_units(units, assigned)
    assert out.loc[out["filer_id"] == "tu_head", "spm_unit_id"].iloc[0] == "H2_primary"
    assert out.loc[out["filer_id"] == "tu_room", "spm_unit_id"].iloc[0] == "H2_unrel_2"


def test_attach_unknown_primary_filer_id_becomes_na():
    persons = _make_persons([
        {"SERIALNO": "H3", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 40},
    ])
    assigned = build_spm_unit_assignment(persons)
    units = pd.DataFrame([
        {"filer_id": "tu_missing", "primary_filer_id": "DOES_NOT_EXIST", "filing_status": "single"},
    ])
    out = attach_spm_unit_id_to_tax_units(units, assigned)
    assert pd.isna(out["spm_unit_id"].iloc[0])


@pytest.mark.parametrize("relshipp,expected_partition", [
    (21, "primary"),    # opposite-sex spouse
    (22, "primary"),    # opposite-sex partner
    (25, "primary"),    # biological child
    (29, "primary"),    # parent
    (31, "primary"),    # parent-in-law
    (33, "primary"),    # other relative
    (35, "primary"),    # foster child
    (34, "unrel"),      # roommate (adult)
    (36, "unrel"),      # other non-relative (adult)
    (37, "gq"),         # institutional group quarters
    (38, "gq"),         # non-institutional group quarters
])
def test_relshipp_partition_mapping(relshipp, expected_partition):
    """Each canonical RELSHIPP code lands in the correct SPM partition when
    the second household member is an adult (age 40)."""
    persons = _make_persons([
        {"SERIALNO": "M1", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 50},
        {"SERIALNO": "M1", "SPORDER": 2, "RELSHIPP": relshipp, "AGEP": 40},
    ])
    out = build_spm_unit_assignment(persons)
    member = out.iloc[1]["spm_unit_id"]
    if expected_partition == "primary":
        assert member == "M1_primary"
    elif expected_partition == "unrel":
        assert member == "M1_unrel_2"
    else:
        assert pd.isna(member)


def test_attach_with_person_id_as_index():
    """When the persons frame has person_id as its index (the constructor
    convention), the attach function must still work."""
    persons = _make_persons([
        {"SERIALNO": "H4", "SPORDER": 1, "RELSHIPP": 20, "AGEP": 40},
    ])
    assigned = build_spm_unit_assignment(persons)
    assigned = assigned.set_index("person_id")
    units = pd.DataFrame([
        {"filer_id": "tu", "primary_filer_id": "H4_1", "filing_status": "single"},
    ])
    out = attach_spm_unit_id_to_tax_units(units, assigned)
    assert out["spm_unit_id"].iloc[0] == "H4_primary"
