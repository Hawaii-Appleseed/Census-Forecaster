"""Tests for tax-unit → SPM-unit aggregation."""
from __future__ import annotations

import pandas as pd
import pytest

from tax_modeler.errors import DataValidationError
from tax_modeler.poverty.spm_aggregation import aggregate_to_spm_units


def _persons(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _tax_units(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_aggregate_sums_credits_across_tax_units():
    """Two tax units in the same SPM unit → eitc/ctc summed."""
    persons = _persons([
        {"SERIALNO": "H1", "SPORDER": 1, "AGEP": 50, "PWGTP": 100, "WGTP": 100,
         "spm_unit_id": "H1_primary"},
        {"SERIALNO": "H1", "SPORDER": 2, "AGEP": 25, "PWGTP": 100, "WGTP": 100,
         "spm_unit_id": "H1_primary"},
    ])
    tu = _tax_units([
        {"filer_id": "tu1", "SERIALNO": "H1", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 30000, "eitc_amount": 1000, "ctc_refundable": 500,
         "spm_unit_id": "H1_primary", "hh_weight": 100},
        {"filer_id": "tu2", "SERIALNO": "H1", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 20000, "eitc_amount": 1500, "ctc_refundable": 250,
         "spm_unit_id": "H1_primary", "hh_weight": 100},
    ])
    out = aggregate_to_spm_units(tu, persons)
    assert len(out) == 1
    row = out.iloc[0]
    assert row["eitc_amount"] == 2500
    assert row["ctc_refundable"] == 750
    assert row["total_cash_income"] == 50000
    assert row["n_tax_units_pooled"] == 2
    assert row["is_multi_tax_unit_spm"]


def test_aggregate_keeps_geography_from_household():
    persons = _persons([
        {"SERIALNO": "H2", "SPORDER": 1, "AGEP": 40, "PWGTP": 50, "WGTP": 50,
         "spm_unit_id": "H2_primary"},
        {"SERIALNO": "H2", "SPORDER": 2, "AGEP": 12, "PWGTP": 50, "WGTP": 50,
         "spm_unit_id": "H2_primary"},
    ])
    tu = _tax_units([
        {"filer_id": "tu1", "SERIALNO": "H2", "PUMA": "0200", "tenure": "owner_with_mortgage",
         "county": "Maui", "house_district": 7, "senate_district": 4,
         "total_cash_income": 50000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H2_primary", "hh_weight": 50},
    ])
    out = aggregate_to_spm_units(tu, persons)
    row = out.iloc[0]
    assert row["PUMA"] == "0200"
    assert row["county"] == "Maui"
    assert row["tenure"] == "owner_with_mortgage"
    assert row["house_district"] == 7
    assert row["senate_district"] == 4


def test_aggregate_uses_household_weight_not_tax_unit_weight():
    """The aggregated weight column must be the raw WGTP from persons,
    not the calibrated tax-unit weight."""
    persons = _persons([
        {"SERIALNO": "H3", "SPORDER": 1, "AGEP": 40, "PWGTP": 200, "WGTP": 200,
         "spm_unit_id": "H3_primary"},
        {"SERIALNO": "H3", "SPORDER": 2, "AGEP": 38, "PWGTP": 200, "WGTP": 200,
         "spm_unit_id": "H3_primary"},
    ])
    tu = _tax_units([
        {"filer_id": "tu1", "SERIALNO": "H3", "PUMA": "0300", "tenure": "renter",
         "county": "Hawaii", "house_district": 1, "senate_district": 1,
         "total_cash_income": 80000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H3_primary",
         "weight": 999,  # tax-unit calibrated weight — should NOT be used
         "hh_weight": 200},
    ])
    out = aggregate_to_spm_units(tu, persons)
    assert out["weight"].iloc[0] == 200  # WGTP, not 999


def test_n_persons_computed_from_persons_frame():
    persons = _persons([
        {"SERIALNO": "H4", "SPORDER": i, "AGEP": age, "PWGTP": 100, "WGTP": 100,
         "spm_unit_id": "H4_primary"}
        for i, age in [(1, 45), (2, 42), (3, 10), (4, 7)]
    ])
    tu = _tax_units([
        {"filer_id": "tu1", "SERIALNO": "H4", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 60000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H4_primary", "hh_weight": 100},
    ])
    out = aggregate_to_spm_units(tu, persons)
    row = out.iloc[0]
    assert row["n_persons"] == 4
    assert row["n_adults"] == 2
    assert row["n_children"] == 2


def test_unrelated_adult_aggregated_independently():
    """Tax unit headed by an unrelated adult → its own SPM unit row."""
    persons = _persons([
        {"SERIALNO": "H5", "SPORDER": 1, "AGEP": 40, "PWGTP": 100, "WGTP": 100,
         "spm_unit_id": "H5_primary"},
        {"SERIALNO": "H5", "SPORDER": 2, "AGEP": 30, "PWGTP": 100, "WGTP": 100,
         "spm_unit_id": "H5_unrel_2"},
    ])
    tu = _tax_units([
        {"filer_id": "tu_head", "SERIALNO": "H5", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 50000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H5_primary", "hh_weight": 100},
        {"filer_id": "tu_room", "SERIALNO": "H5", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 30000, "eitc_amount": 200, "ctc_refundable": 0,
         "spm_unit_id": "H5_unrel_2", "hh_weight": 100},
    ])
    out = aggregate_to_spm_units(tu, persons)
    assert len(out) == 2
    primary_row = out[out["spm_unit_id"] == "H5_primary"].iloc[0]
    unrel_row = out[out["spm_unit_id"] == "H5_unrel_2"].iloc[0]
    assert primary_row["n_persons"] == 1
    assert primary_row["filing_status"] == "single"
    assert unrel_row["n_persons"] == 1
    assert unrel_row["eitc_amount"] == 200


def test_classify_spm_household_type():
    """The filing_status proxy correctly maps composition → label."""
    # Use the same scenarios via a single fixture and check the labels.
    persons = _persons([
        # Single adult, no kids (file 1)
        {"SERIALNO": "A", "SPORDER": 1, "AGEP": 35, "PWGTP": 100, "WGTP": 100, "spm_unit_id": "A_primary"},
        # Single parent with one kid (file 2)
        {"SERIALNO": "B", "SPORDER": 1, "AGEP": 32, "PWGTP": 100, "WGTP": 100, "spm_unit_id": "B_primary"},
        {"SERIALNO": "B", "SPORDER": 2, "AGEP": 8, "PWGTP": 100, "WGTP": 100, "spm_unit_id": "B_primary"},
        # Couple with kids (file 3)
        {"SERIALNO": "C", "SPORDER": 1, "AGEP": 40, "PWGTP": 100, "WGTP": 100, "spm_unit_id": "C_primary"},
        {"SERIALNO": "C", "SPORDER": 2, "AGEP": 38, "PWGTP": 100, "WGTP": 100, "spm_unit_id": "C_primary"},
        {"SERIALNO": "C", "SPORDER": 3, "AGEP": 10, "PWGTP": 100, "WGTP": 100, "spm_unit_id": "C_primary"},
    ])
    tu = _tax_units([
        {"filer_id": "a", "SERIALNO": "A", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 25000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "A_primary", "hh_weight": 100},
        {"filer_id": "b", "SERIALNO": "B", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 25000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "B_primary", "hh_weight": 100},
        {"filer_id": "c", "SERIALNO": "C", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 75000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "C_primary", "hh_weight": 100},
    ])
    out = aggregate_to_spm_units(tu, persons)
    out_by_id = out.set_index("spm_unit_id")
    assert out_by_id.loc["A_primary", "filing_status"] == "single"
    assert out_by_id.loc["B_primary", "filing_status"] == "head_of_household"
    assert out_by_id.loc["C_primary", "filing_status"] == "married_filing_jointly"


def test_drops_tax_units_with_null_spm_unit_id():
    persons = _persons([
        {"SERIALNO": "GQ", "SPORDER": 1, "AGEP": 22, "PWGTP": 50, "WGTP": 50,
         "spm_unit_id": pd.NA},
        {"SERIALNO": "H", "SPORDER": 1, "AGEP": 30, "PWGTP": 50, "WGTP": 50,
         "spm_unit_id": "H_primary"},
    ])
    tu = _tax_units([
        {"filer_id": "gq", "SERIALNO": "GQ", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 5000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": pd.NA, "hh_weight": 50},
        {"filer_id": "h", "SERIALNO": "H", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 25000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H_primary", "hh_weight": 50},
    ])
    out = aggregate_to_spm_units(tu, persons)
    assert len(out) == 1
    assert out["spm_unit_id"].iloc[0] == "H_primary"


def test_missing_spm_unit_id_raises():
    persons = _persons([
        {"SERIALNO": "H", "SPORDER": 1, "AGEP": 30, "PWGTP": 50, "WGTP": 50,
         "spm_unit_id": "H_primary"},
    ])
    tu_no_id = _tax_units([
        {"filer_id": "h", "SERIALNO": "H", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 25000, "eitc_amount": 0, "ctc_refundable": 0,
         "hh_weight": 50},
    ])
    with pytest.raises(DataValidationError, match="spm_unit_id"):
        aggregate_to_spm_units(tu_no_id, persons)


def test_replicate_weights_carried_per_spm_unit():
    """WGTP1..N broadcast onto persons are carried verbatim per SPM unit."""
    persons = _persons([
        {"SERIALNO": "H6", "SPORDER": 1, "AGEP": 40, "PWGTP": 100, "WGTP": 100,
         "WGTP1": 110, "WGTP2": 90, "spm_unit_id": "H6_primary"},
        {"SERIALNO": "H6", "SPORDER": 2, "AGEP": 38, "PWGTP": 100, "WGTP": 100,
         "WGTP1": 110, "WGTP2": 90, "spm_unit_id": "H6_primary"},
    ])
    tu = _tax_units([
        {"filer_id": "tu1", "SERIALNO": "H6", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 60000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H6_primary", "hh_weight": 100},
    ])
    out = aggregate_to_spm_units(
        tu, persons, replicate_weight_cols=["WGTP1", "WGTP2"]
    )
    row = out.iloc[0]
    assert row["weight"] == 100  # full-sample WGTP
    assert row["WGTP1"] == 110
    assert row["WGTP2"] == 90


def test_missing_replicate_weight_col_raises():
    persons = _persons([
        {"SERIALNO": "H7", "SPORDER": 1, "AGEP": 40, "PWGTP": 100, "WGTP": 100,
         "WGTP1": 110, "spm_unit_id": "H7_primary"},
    ])
    tu = _tax_units([
        {"filer_id": "tu1", "SERIALNO": "H7", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 60000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H7_primary", "hh_weight": 100},
    ])
    with pytest.raises(DataValidationError, match="replicate_weight_cols"):
        aggregate_to_spm_units(
            tu, persons, replicate_weight_cols=["WGTP1", "WGTP2"]  # WGTP2 absent
        )


def test_multi_puma_within_household_raises():
    """Defensive check: PUMS households are single-PUMA. Tampering raises."""
    persons = _persons([
        {"SERIALNO": "H", "SPORDER": 1, "AGEP": 30, "PWGTP": 50, "WGTP": 50,
         "spm_unit_id": "H_primary"},
        {"SERIALNO": "H", "SPORDER": 2, "AGEP": 28, "PWGTP": 50, "WGTP": 50,
         "spm_unit_id": "H_unrel_2"},
    ])
    tu = _tax_units([
        {"filer_id": "a", "SERIALNO": "H", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 30000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H_primary", "hh_weight": 50},
        {"filer_id": "b", "SERIALNO": "H", "PUMA": "0200", "tenure": "renter",
         "county": "Maui", "house_district": 1, "senate_district": 1,
         "total_cash_income": 30000, "eitc_amount": 0, "ctc_refundable": 0,
         "spm_unit_id": "H_unrel_2", "hh_weight": 50},
    ])
    with pytest.raises(DataValidationError, match="PUMA"):
        aggregate_to_spm_units(tu, persons)


def test_food_excise_credit_counted_once_at_spm_grain():
    """hi_low_income_credit must survive aggregation so compute_spm_resources
    can un-net it from hi_tax_liability when hi_food_excise_amount is also
    present. Before it was carried, the SPM frame counted the credit twice."""
    from tax_modeler.poverty.spm import compute_spm_resources

    persons = _persons([
        {"SERIALNO": "H3", "SPORDER": 1, "AGEP": 35, "PWGTP": 10, "WGTP": 10,
         "spm_unit_id": "H3_primary"},
    ])
    tu = _tax_units([
        {"filer_id": "tu1", "SERIALNO": "H3", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 25_000, "hi_tax_before_credits": 400.0,
         "hi_low_income_credit": 55.0, "hi_tax_liability": 345.0,
         "hi_food_excise_amount": 140.0, "spm_unit_id": "H3_primary", "hh_weight": 10},
    ])
    spm = aggregate_to_spm_units(tu, persons)
    assert spm.iloc[0]["hi_low_income_credit"] == 55.0
    with_food, _ = compute_spm_resources(spm, federal_tax_fallback=False)
    without_food, _ = compute_spm_resources(
        spm.drop(columns=["hi_food_excise_amount", "hi_low_income_credit"]),
        hi_food_excise_col=None, federal_tax_fallback=False)
    # With the dedicated column: 25,000 - 400 + 140. Without: 25,000 - 345.
    assert with_food["spm_resources"].iloc[0] - without_food["spm_resources"].iloc[0] == pytest.approx(140 - 55)
