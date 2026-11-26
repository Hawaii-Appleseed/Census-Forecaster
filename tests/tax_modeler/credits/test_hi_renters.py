"""HI renters credit (HRS 235-55.7): statute schedule, renter and rent tests, and the DOTAX take-up anchor."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.calibration import dotax_renters_claims as claimed
from tax_modeler.credits.hi_renters import (
    FALLBACK_TAKEUP,
    TAKEUP_GUARD,
    compute_hi_renters_for_units,
    fit_renters_takeup,
)
from tax_modeler.errors import ConfigError

FULL = {"takeup_pct": 1.0}


def _units(rows):
    return pd.DataFrame(rows, columns=[
        "income", "filing_status", "num_dependents", "primary_agep", "secondary_agep",
        "tenure", "annual_rent", "weight"])


def _one(income=10_000, fs="single", deps=0, age=40, age2=0, tenure="renter", rent=12_000.0, w=1):
    return (income, fs, deps, age, age2, tenure, rent, w)


def _credit(rows, **ov):
    return compute_hi_renters_for_units(_units(rows), overrides={**FULL, **ov})["hi_renters_amount"].tolist()


def test_fifty_dollars_per_exemption_no_agi_tiers():
    out = _credit([
        _one(income=1_000), _one(income=20_000), _one(income=29_999),   # same $50 at every AGI under $30K
        _one(income=30_000),                                            # "less than $30,000"
        _one(fs="married_filing_jointly"),                               # filer + spouse
        _one(fs="head_of_household", deps=2),
        _one(fs="married_filing_jointly", deps=3, income=29_000),
    ])
    assert out == [50.0, 50.0, 50.0, 0.0, 100.0, 150.0, 250.0]


def test_agi_limit_is_30k_for_every_status_not_the_old_caps():
    out = _credit([_one(income=35_000, fs="married_filing_jointly"), _one(income=25_000, fs="married_filing_separately"),
                   _one(income=32_000, fs="head_of_household")])
    assert out == [0.0, 50.0, 0.0]


def test_age_65_doubles_each_elderly_filer():
    out = _credit([
        _one(age=64), _one(age=65),
        _one(fs="married_filing_jointly", age=70, age2=40),
        _one(fs="married_filing_jointly", age=70, age2=66),
        _one(fs="married_filing_jointly", age=40, age2=66, deps=1),
    ])
    assert out == [50.0, 100.0, 150.0, 200.0, 200.0]
    # the spouse's age counts only on a joint return
    assert _credit([_one(fs="single", age=40, age2=80)]) == [50.0]


def test_num_exemptions_column_wins():
    u = _units([_one(deps=5)])
    u["num_exemptions"] = 2.0
    assert compute_hi_renters_for_units(u, overrides=FULL)["hi_renters_amount"].tolist() == [100.0]


def test_owners_get_nothing_renters_only():
    out = _credit([_one(), _one(tenure="owner_no_mortgage"), _one(tenure="owner_with_mortgage")])
    assert out == [50.0, 0.0, 0.0]
    assert _credit([_one(), _one(tenure="owner_no_mortgage")], renters_only=False) == [50.0, 50.0]


def test_rent_must_exceed_1000():
    assert _credit([_one(rent=1_000.0), _one(rent=1_001.0), _one(rent=0.0)]) == [0.0, 50.0, 0.0]


def test_no_tenure_or_rent_columns_keeps_income_test_and_uses_fallback_takeup():
    u = _units([_one()]).drop(columns=["tenure", "annual_rent"])
    out = compute_hi_renters_for_units(u)["hi_renters_amount"]
    assert out.iloc[0] == pytest.approx(50.0 * FALLBACK_TAKEUP)


def test_reform_parameters():
    assert _credit([_one()], per_exemption_amount=75.0) == [75.0]
    assert _credit([_one(income=35_000)], income_limit=40_000.0) == [50.0]
    assert _credit([_one(income=35_000)], income_threshold_factor=1.5) == [50.0]
    with pytest.raises(ConfigError):
        _credit([_one()], credit_under_15k=150.0)


def test_fit_matches_dotax_dollars_for_anchor_year():
    u = _units([_one(w=100_000), _one(tenure="owner_no_mortgage", w=100_000)])
    for ty in (2022, 2023):
        out = compute_hi_renters_for_units(u, tax_year=ty)
        dollars = float((out["weight"] * out["hi_renters_amount"]).sum())
        assert dollars == pytest.approx(claimed.dollars_M(ty) * 1e6, rel=1e-9)


def test_fit_caps_year_at_latest_and_ignores_reform_overrides():
    u = _units([_one(w=100_000)])
    late = compute_hi_renters_for_units(u, tax_year=2030)["hi_renters_amount"].iloc[0]
    assert late * 100_000 == pytest.approx(claimed.dollars_M(claimed.latest_year()) * 1e6)
    # a reform that raises the amount 50% raises dollars 50%: the fit does not undo it
    up = compute_hi_renters_for_units(u, tax_year=2030, overrides={"amount_pct": 1.5})["hi_renters_amount"].iloc[0]
    assert up == pytest.approx(1.5 * late)


def test_guard_raises_when_fit_is_implausible():
    lo, hi = TAKEUP_GUARD
    target = claimed.dollars_M(2022) * 1e6
    with pytest.raises(ConfigError):
        fit_renters_takeup(target / (lo / 2), 2022)
    with pytest.raises(ConfigError):
        fit_renters_takeup(target / (hi * 2), 2022)
    with pytest.raises(ConfigError):
        fit_renters_takeup(0.0, 2022)
    assert fit_renters_takeup(target / 0.2, 2022) == pytest.approx(0.2)


def test_bundled_dotax_series():
    assert claimed.years() == [2018, 2019, 2020, 2021, 2022, 2023]
    assert claimed.claims(2022) == 16_925
    assert claimed.dollars_M(2022) == pytest.approx(1.675)
    assert np.isclose(claimed.dollars_M(2023), 1.64455)


def test_attach_rent_is_annual_contract_rent_and_zero_without_cash_rent():
    from tax_modeler.pipeline import attach_rent

    hh = pd.DataFrame({"SERIALNO": ["a", "b", "c"], "RNTP": [1_000.0, np.nan, np.nan], "TEN": [3, 1, 4]})
    u = pd.DataFrame({"hh_id": ["a", "b", "c", "zz"]})
    assert attach_rent(u, hh, key="hh_id")["annual_rent"].tolist() == [12_000.0, 0.0, 0.0, 0.0]
    assert "annual_rent" not in attach_rent(u, hh.drop(columns="RNTP"), key="hh_id").columns
