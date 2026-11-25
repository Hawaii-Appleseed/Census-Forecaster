"""HI renters credit: renter test, schedule, and the DOTAX take-up anchor."""
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


def _units(rows):
    return pd.DataFrame(rows, columns=["income", "filing_status", "tenure", "weight"])


def test_schedule_tiers_and_caps_at_full_takeup():
    u = _units([
        (10_000, "single", "renter", 1), (20_000, "single", "renter", 1),
        (28_000, "single", "renter", 1), (31_000, "single", "renter", 1),
        (39_000, "married_filing_jointly", "renter", 1), (21_000, "married_filing_separately", "renter", 1),
    ])
    out = compute_hi_renters_for_units(u, overrides={"takeup_pct": 1.0})["hi_renters_amount"].tolist()
    assert out == [150.0, 100.0, 50.0, 0.0, 50.0, 0.0]


def test_owners_get_nothing_renters_only():
    u = _units([(10_000, "single", "renter", 1), (10_000, "single", "owner_no_mortgage", 1),
                (10_000, "single", "owner_with_mortgage", 1)])
    out = compute_hi_renters_for_units(u, overrides={"takeup_pct": 1.0})["hi_renters_amount"]
    assert out.tolist() == [150.0, 0.0, 0.0]
    off = compute_hi_renters_for_units(u, overrides={"takeup_pct": 1.0, "renters_only": False})
    assert off["hi_renters_amount"].tolist() == [150.0] * 3


def test_no_tenure_column_keeps_income_only_and_uses_fallback_takeup():
    u = _units([(10_000, "single", "renter", 1)]).drop(columns="tenure")
    out = compute_hi_renters_for_units(u)["hi_renters_amount"]
    assert out.iloc[0] == pytest.approx(150.0 * FALLBACK_TAKEUP)


def test_fit_matches_dotax_dollars_for_anchor_year():
    # 100K weighted renter units at $150: $15M at full take-up.
    u = _units([(10_000, "single", "renter", 100_000), (10_000, "single", "owner_no_mortgage", 100_000)])
    for ty in (2022, 2023):
        out = compute_hi_renters_for_units(u, tax_year=ty)
        dollars = float((out["weight"] * out["hi_renters_amount"]).sum())
        assert dollars == pytest.approx(claimed.dollars_M(ty) * 1e6, rel=1e-9)


def test_fit_caps_year_at_latest_and_ignores_reform_overrides():
    u = _units([(10_000, "single", "renter", 100_000)])
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
