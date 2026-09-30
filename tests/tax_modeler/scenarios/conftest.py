"""Fixtures for the synthetic $1M+ tail tests (top_income_synthesis,
act24_population)."""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.artifacts import load_canonical_deduction_params
from tax_modeler.liability.federal import add_federal_income_columns
from tax_modeler.pipeline import compute_base_tax

CAL_TAX_YEAR = 2023


@pytest.fixture(scope="session")
def ded_params():
    return load_canonical_deduction_params()


@pytest.fixture(scope="session")
def score(ded_params):
    """Re-scoring on the calibration basis, as build_units does."""
    return lambda u: compute_base_tax(u, deduction_params=ded_params, tax_year=CAL_TAX_YEAR)


@pytest.fixture
def tail_units() -> pd.DataFrame:
    """Three PUMS units and a five-row synthetic tail shaped like the Pareto
    synthesizer's (Honolulu, rising gains shares, weights falling with income)."""
    df = pd.DataFrame({
        "income": [45_000.0, 180_000.0, 650_000.0, 1.4e6, 3.0e6, 6.8e6, 1.5e7, 3.4e7],
        "filing_status": ["single", "married_filing_jointly", "single",
                          "married_filing_jointly", "single", "married_filing_jointly",
                          "head_of_household", "married_filing_separately"],
        "num_dependents": [0, 2, 0, 2, 0, 1, 1, 0],
        "weight": [20.0, 15.0, 3.0, 800.0, 330.0, 70.0, 30.0, 6.0],
        "is_synthetic_ultra_high": [False, False, False, True, True, True, True, True],
        "synthetic_cg_share": [0.0, 0.0, 0.0, 0.21, 0.25, 0.30, 0.45, 0.47],
        "county": ["Maui", "Honolulu", "Hawaii"] + ["Honolulu"] * 5,
    })
    df["agi"] = df["synthetic_total_income"] = df["income"]
    df["earned_income"] = df["primary_wagp"] = df["income"] * (1 - df["synthetic_cg_share"])
    df["investment_income"] = df["primary_intp"] = df["income"] * df["synthetic_cg_share"]
    return add_federal_income_columns(df)
