"""Tests for the money-income term of tax_modeler.poverty.spm.compute_spm_resources."""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.poverty.spm import SPMColumns, compute_spm_resources


def _unit(**cols) -> pd.DataFrame:
    """One unit with no taxes, credits or payroll: resources = money income."""
    return pd.DataFrame([{"total_cash_income": 20_000.0, "hi_tax_liability": 0.0,
                          "earned_income": 0.0, **cols}])


def test_resources_read_aged_money_income_on_a_projected_frame():
    """A projected frame's TCI is in base-year dollars; the aged column wins."""
    out, meta = compute_spm_resources(_unit(spm_money_income=23_000.0), federal_tax_fallback=False)
    assert out["spm_resources"].iloc[0] == pytest.approx(23_000.0)
    assert "spm_money_income" in meta.used_columns
    assert "total_cash_income" not in meta.used_columns


def test_resources_read_tci_without_the_aged_column():
    out, meta = compute_spm_resources(_unit(), federal_tax_fallback=False)
    assert out["spm_resources"].iloc[0] == pytest.approx(20_000.0)
    assert "total_cash_income" in meta.used_columns


def test_explicit_money_income_col_overrides_detection():
    out, _ = compute_spm_resources(
        _unit(spm_money_income=23_000.0), columns=SPMColumns(money_income="total_cash_income"),
        federal_tax_fallback=False,
    )
    assert out["spm_resources"].iloc[0] == pytest.approx(20_000.0)


def test_aged_money_income_survives_spm_aggregation():
    from tax_modeler.poverty.spm_aggregation import aggregate_to_spm_units

    persons = pd.DataFrame([
        {"SERIALNO": "H1", "SPORDER": 1, "AGEP": 40, "PWGTP": 10, "WGTP": 10, "spm_unit_id": "H1_primary"},
        {"SERIALNO": "H1", "SPORDER": 2, "AGEP": 70, "PWGTP": 10, "WGTP": 10, "spm_unit_id": "H1_primary"},
    ])
    common = {"SERIALNO": "H1", "PUMA": "0100", "tenure": "renter", "county": "Honolulu",
              "house_district": 1, "senate_district": 1, "spm_unit_id": "H1_primary",
              "hh_weight": 10, "hi_tax_liability": 0.0, "earned_income": 0.0}
    tu = pd.DataFrame([
        {**common, "filer_id": "tu1", "total_cash_income": 30_000.0, "spm_money_income": 34_000.0},
        {**common, "filer_id": "tu2", "total_cash_income": 12_000.0, "spm_money_income": 13_000.0},
    ])
    spm = aggregate_to_spm_units(tu, persons)
    assert spm.iloc[0]["spm_money_income"] == pytest.approx(47_000.0)
    out, _ = compute_spm_resources(spm, federal_tax_fallback=False)
    assert out["spm_resources"].iloc[0] == pytest.approx(47_000.0)


def test_federal_fallback_extrapolates_past_the_rev_proc_when_asked():
    """TY2028 has no Rev. Proc.: without the flag the calculator raises and the
    fallback drops to the flat rate; with it, the extrapolated schedule applies."""
    unit = _unit(total_cash_income=60_000.0, federal_agi=60_000.0, filing_status="single")
    strict, strict_meta = compute_spm_resources(unit, tax_year=2028)
    extrap, extrap_meta = compute_spm_resources(unit, tax_year=2028, extrapolate=True)
    assert strict_meta.federal_tax_source == "fallback_rate"
    assert extrap_meta.federal_tax_source == "computed"
    assert strict["spm_resources"].iloc[0] == pytest.approx(60_000.0 * 0.90)
    assert extrap["spm_resources"].iloc[0] != pytest.approx(strict["spm_resources"].iloc[0])


def test_the_care_credit_is_added_once_and_survives_aggregation():
    """HRS §235-55.6's credit is refundable and not netted into hi_tax_liability, so SPM resources add it
    exactly once (and the childcare expense subtracted stays gross)."""
    from tax_modeler.poverty.spm_aggregation import aggregate_to_spm_units

    out, meta = compute_spm_resources(_unit(hi_cdcc_amount=1_200.0, childcare_expense_amount=3_000.0),
                                      federal_tax_fallback=False)
    assert out["spm_resources"].iloc[0] == pytest.approx(20_000.0 + 1_200.0 - 3_000.0)
    assert "hi_cdcc_amount" in meta.used_columns and "hi_cdcc" not in meta.zeroed_components
    assert "hi_cdcc" in compute_spm_resources(_unit(), federal_tax_fallback=False)[1].zeroed_components

    persons = pd.DataFrame([
        {"SERIALNO": "H1", "SPORDER": 1, "AGEP": 35, "PWGTP": 10, "WGTP": 10, "spm_unit_id": "H1_primary"}])
    common = {"SERIALNO": "H1", "PUMA": "0100", "tenure": "renter", "county": "Honolulu",
              "house_district": 1, "senate_district": 1, "spm_unit_id": "H1_primary",
              "hh_weight": 10, "hi_tax_liability": 0.0, "earned_income": 0.0, "total_cash_income": 10_000.0}
    tu = pd.DataFrame([{**common, "filer_id": "tu1", "hi_cdcc_amount": 500.0},
                       {**common, "filer_id": "tu2", "hi_cdcc_amount": 250.0}])
    assert aggregate_to_spm_units(tu, persons).iloc[0]["hi_cdcc_amount"] == pytest.approx(750.0)
