"""The tax-unit calibration ratio carried into the SPM weights
(``poverty.weight_basis``, ``aggregate_to_spm_units(calibration_ratio_col=)``)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tax_modeler.errors import DataValidationError
from tax_modeler.poverty.spm_aggregation import aggregate_to_spm_units
from tax_modeler.poverty.weight_basis import (
    CALIBRATION_RATIO_COL,
    attach_calibration_ratio,
    pums_weight,
    tax_unit_calibration_ratio,
)


def test_ratio_is_weight_over_the_pums_weight_of_the_hybrid_rule():
    units = pd.DataFrame({
        "filing_status": ["single", "married_filing_jointly", "head_of_household", "single"],
        "num_dependents": [0, 1, 2, 0],
        "weight": [8.2, 20.0, 26.0, 0.1],          # 0.82 x 10, 1.0 x 20, 1.30 x 20, floor
        "hh_weight": [20.0, 20.0, 20.0, 0.0],
        "person_weight_sum": [10.0, 55.0, 40.0, 0.0],
    })
    # one-person unit -> PWGTP; spouse or dependents -> WGTP
    np.testing.assert_allclose(pums_weight(units), [10.0, 20.0, 20.0, 0.0])
    np.testing.assert_allclose(tax_unit_calibration_ratio(units), [0.82, 1.0, 1.30, 1.0])
    out = attach_calibration_ratio(units)
    assert CALIBRATION_RATIO_COL in out.columns
    assert CALIBRATION_RATIO_COL not in units.columns


def test_ratio_falls_back_to_the_snapshot_then_to_one():
    snap = pd.DataFrame({"weight": [3.0, 5.0], "weight_uncal": [1.5, 0.0]})
    np.testing.assert_allclose(tax_unit_calibration_ratio(snap), [2.0, 1.0])
    bare = pd.DataFrame({"weight": [3.0, 5.0]})
    np.testing.assert_allclose(tax_unit_calibration_ratio(bare), [1.0, 1.0])


def _one_household():
    persons = pd.DataFrame([
        {"SERIALNO": "H1", "SPORDER": 1, "AGEP": 50, "PWGTP": 100, "WGTP": 100,
         "WGTP1": 110, "WGTP2": 90, "spm_unit_id": "H1_primary"},
        {"SERIALNO": "H1", "SPORDER": 2, "AGEP": 25, "PWGTP": 100, "WGTP": 100,
         "WGTP1": 110, "WGTP2": 90, "spm_unit_id": "H1_primary"},
    ])
    tu = pd.DataFrame([
        {"filer_id": "tu1", "SERIALNO": "H1", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 30000, "eitc_amount": 1000, "ctc_refundable": 500,
         "spm_unit_id": "H1_primary", "hh_weight": 100, CALIBRATION_RATIO_COL: 2.0},
        {"filer_id": "tu2", "SERIALNO": "H1", "PUMA": "0100", "tenure": "renter",
         "county": "Honolulu", "house_district": 1, "senate_district": 1,
         "total_cash_income": 20000, "eitc_amount": 1500, "ctc_refundable": 250,
         "spm_unit_id": "H1_primary", "hh_weight": 100, CALIBRATION_RATIO_COL: 1.0},
    ])
    return tu, persons


def test_mean_ratio_scales_the_spm_weight_and_every_replicate():
    tu, persons = _one_household()
    raw = aggregate_to_spm_units(tu, persons, replicate_weight_cols=["WGTP1", "WGTP2"])
    carried = aggregate_to_spm_units(
        tu, persons, replicate_weight_cols=["WGTP1", "WGTP2"],
        calibration_ratio_col=CALIBRATION_RATIO_COL,
    )
    assert raw.iloc[0]["weight"] == 100
    assert "calibration_ratio" not in raw.columns
    r = carried.iloc[0]
    assert r["calibration_ratio"] == pytest.approx(1.5)      # mean of 2.0 and 1.0
    assert r["weight"] == pytest.approx(150.0)
    assert r["weight_r01"] == pytest.approx(165.0)           # auto-detected WGTP1
    assert r["WGTP1"] == pytest.approx(165.0)                # explicit column, same ratio
    assert r["WGTP2"] == pytest.approx(135.0)
    # Dollar sums are untouched.
    assert r["eitc_amount"] == 2500


def test_missing_ratio_column_raises():
    tu, persons = _one_household()
    with pytest.raises(DataValidationError, match="calibration_ratio_col"):
        aggregate_to_spm_units(tu.drop(columns=CALIBRATION_RATIO_COL), persons,
                               calibration_ratio_col=CALIBRATION_RATIO_COL)
