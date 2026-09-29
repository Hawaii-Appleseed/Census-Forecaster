"""Tests for ``tax_modeler.liability.federal.compute_federal_income_tax_for_units``."""
from __future__ import annotations

import pandas as pd
import pytest

from tax_modeler.liability.federal import compute_federal_income_tax_for_units


def _frame(rows):
    return pd.DataFrame(rows)


def test_low_income_owes_zero_federal_tax_after_sd():
    """A single filer below the federal SD owes $0 federal income tax."""
    df = _frame([{"filing_status": "single", "total_cash_income": 10_000}])
    out = compute_federal_income_tax_for_units(df, tax_year=2024)
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(0.0, abs=1e-6)


def test_mfj_at_standard_deduction_owes_zero():
    """An MFJ household exactly at the SD owes $0."""
    df = _frame([{"filing_status": "married_filing_jointly",
                  "total_cash_income": 29_200}])
    out = compute_federal_income_tax_for_units(df, tax_year=2024)
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(0.0, abs=1e-6)


def test_mfj_inside_10pct_bracket():
    """TY2024 MFJ at $40k income: SD=$29,200 → taxable $10,800 × 10% = $1,080."""
    df = _frame([{"filing_status": "married_filing_jointly",
                  "total_cash_income": 40_000}])
    out = compute_federal_income_tax_for_units(df, tax_year=2024)
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(1_080.0, abs=1.0)


def test_single_into_12pct_bracket():
    """TY2024 single at $50k: SD=$14,600 → taxable $35,400.
    Bracket walk: $11,600 × 10% + ($35,400-$11,600) × 12% = $1,160 + $2,856 = $4,016."""
    df = _frame([{"filing_status": "single", "total_cash_income": 50_000}])
    out = compute_federal_income_tax_for_units(df, tax_year=2024)
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(4_016.0, abs=1.0)


def test_nonrefundable_ctc_offsets_tax():
    """A filer with $4,000 bracket tax and $2,000 nonref CTC pays $2,000."""
    df = _frame([{
        "filing_status": "single",
        "total_cash_income": 50_000,
        "ctc_nonrefundable": 2_000.0,
    }])
    out = compute_federal_income_tax_for_units(df, tax_year=2024)
    # bracket tax = $4,016, minus $2,000 nonref CTC = $2,016
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(2_016.0, abs=1.0)


def test_ctc_cannot_push_liability_below_zero():
    """Nonrefundable CTC offsets bracket tax to zero floor, not negative."""
    df = _frame([{
        "filing_status": "married_filing_jointly",
        "total_cash_income": 35_000,
        "ctc_nonrefundable": 10_000.0,
    }])
    out = compute_federal_income_tax_for_units(df, tax_year=2024)
    assert out["federal_tax_liability"].iloc[0] >= 0.0
    # bracket tax = $580; nonref CTC $10k caps liability at $0
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(0.0, abs=1e-6)


def test_year_specific_brackets_2022():
    """TY2022 MFJ SD=$25,900. At $40k income: taxable $14,100 × 10% = $1,410."""
    df = _frame([{"filing_status": "married_filing_jointly",
                  "total_cash_income": 40_000}])
    out = compute_federal_income_tax_for_units(df, tax_year=2022)
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(1_410.0, abs=1.0)


def test_hoh_uses_head_of_household_brackets():
    """HoH brackets differ from single — confirm $20k MFJ-equivalent uses HoH SD."""
    df = _frame([{"filing_status": "head_of_household",
                  "total_cash_income": 25_000}])
    out = compute_federal_income_tax_for_units(df, tax_year=2024)
    # HoH SD 2024 = $21,900 → taxable $3,100 × 10% = $310
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(310.0, abs=1.0)


def test_unsupported_year_raises():
    df = _frame([{"filing_status": "single", "total_cash_income": 50_000}])
    with pytest.raises(KeyError):
        compute_federal_income_tax_for_units(df, tax_year=2030)


def test_2025_standard_deduction_reflects_pl_119_21():
    from tax_modeler.liability.federal import federal_tax_before_credits
    # $15,750 SD: $15,750 of income owes nothing, $16,750 owes 10% × 1_000.
    assert federal_tax_before_credits(15_750, "single", tax_year=2025)[0] == 0
    assert federal_tax_before_credits(16_750, "single", tax_year=2025)[0] == pytest.approx(100)


def test_2026_hoh_brackets_rev_proc_2025_32():
    from tax_modeler.liability.federal import federal_tax_before_credits
    # Taxable $67,450 (top of 12% HoH bracket) → $7,740 per Table 2.
    tax = federal_tax_before_credits(67_450 + 24_150, "head_of_household", tax_year=2026)
    assert tax[0] == pytest.approx(7_740)


def test_federal_extrapolation_grows_and_rounds():
    from tax_modeler.liability.federal import _federal_parameters_for_year
    sd26, br26 = _federal_parameters_for_year(2026)
    sd28, br28 = _federal_parameters_for_year(2028, extrapolate=True)
    assert sd28["single"] > sd26["single"] and sd28["single"] % 50 == 0
    assert br28["single"][0][0] > br26["single"][0][0] and br28["single"][0][0] % 25 == 0


def test_taxable_social_security_tiers():
    import numpy as np
    from tax_modeler.liability.federal import taxable_social_security
    other = np.array([10_000.0, 20_000.0, 40_000.0])
    ss = np.array([20_000.0, 20_000.0, 20_000.0])
    status = np.array(["single"] * 3)
    t = taxable_social_security(other, ss, status)
    # Provisional 20K < $25K → 0; 30K → 50% × 5K; 50K → capped at 85% × 20K.
    assert t.tolist() == pytest.approx([0.0, 2_500.0, 17_000.0])


def test_add_federal_income_columns_w2_wages_and_ss():
    from tax_modeler.liability.federal import (
        FEDERAL_W2_WAGE_FACTOR, add_federal_income_columns,
    )
    df = pd.DataFrame([
        {"filing_status": "single", "income": 50_000.0, "earned_income": 50_000.0,
         "primary_wagp": 50_000.0, "primary_ssp_full": 0.0},
        # SS-only retiree: model income holds 85% of benefits; §86 taxes none.
        {"filing_status": "single", "income": 17_000.0, "earned_income": 0.0,
         "primary_wagp": 0.0, "primary_ssp_full": 20_000.0},
    ])
    out = add_federal_income_columns(df)
    cut = (1 - FEDERAL_W2_WAGE_FACTOR) * 50_000
    assert out["federal_earned_income"].tolist() == pytest.approx([50_000 - cut, 0.0])
    assert out["federal_agi"].tolist() == pytest.approx([50_000 - cut, 0.0])
    assert out["income"].tolist() == [50_000.0, 17_000.0]  # Hawaii-side column untouched


def test_federal_tax_defaults_to_federal_agi():
    df = pd.DataFrame([{"filing_status": "single", "total_cash_income": 60_000.0,
                        "federal_agi": 20_000.0}])
    out = compute_federal_income_tax_for_units(df, tax_year=2024)
    # 10% × (20_000 − 14_600) = 540, not tax on $60K.
    assert out["federal_tax_liability"].iloc[0] == pytest.approx(540)


def test_ctc_and_eitc_read_federal_columns():
    from tax_modeler.credits.ctc import calculate_ctc
    from tax_modeler.credits.eitc import calculate_eitc
    kids = [{'age': 6, 'relationship': 25, 'citizenship': 1},
            {'age': 9, 'relationship': 25, 'citizenship': 1}]
    unit = {'filing_status': 'head_of_household', 'income': 40_000.0,
            'earned_income': 40_000.0, 'investment_income': 0.0,
            'dependents': kids, 'dependents_details': kids, 'num_qualifying_children': 2}
    fed = dict(unit, federal_agi=30_000.0, federal_earned_income=30_000.0)
    assert calculate_eitc(fed, tax_year=2024)['eitc_amount'] > calculate_eitc(unit, tax_year=2024)['eitc_amount']
    assert calculate_ctc(fed, tax_year=2024)['ctc_refundable'] > calculate_ctc(unit, tax_year=2024)['ctc_refundable']
