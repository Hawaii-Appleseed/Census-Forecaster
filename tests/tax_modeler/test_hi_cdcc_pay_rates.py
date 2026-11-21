"""The probability that a family with a child under 13 pays for care, by AGI class (hi_cdcc.pay_probability), and the
pooled donor file behind the expenses (scripts/pool_cps_care_donors.py)."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from tax_modeler.credits import hi_cdcc


def test_the_rate_is_the_class_rate_and_steps_at_the_edges():
    r = hi_cdcc.pay_rates()
    assert len(r["rates"]) == len(r["edges"]) + 1 and r["edges"] == sorted(r["edges"])
    for i, rate in enumerate(r["rates"]):
        agi = r["edges"][i - 1] if i else -1_000.0              # a class starts at its lower edge (inclusive)
        assert hi_cdcc.pay_probability(agi) == pytest.approx(rate)
    assert hi_cdcc.pay_probability(r["edges"][0] - 0.01) == r["rates"][0] != hi_cdcc.pay_probability(r["edges"][0])
    assert np.allclose(hi_cdcc.pay_probability(np.array([np.nan, 20_000.0])), r["rates"][0])


def test_rates_are_probabilities_and_below_50k_is_the_donor_rate():
    r = hi_cdcc.pay_rates()
    assert all(0 < x <= 1 for x in r["rates"])
    fam = pd.read_parquet(hi_cdcc.DONOR_FAMILIES_PATH)
    low = fam[fam["income"] < 50_000.0]
    rate = float((low["weight"] * (low["expense"] > 0)).sum() / low["weight"].sum())
    assert r["rates"][0] == pytest.approx(rate)
    assert r["below_50k"]["paying"] == int((low["expense"] > 0).sum())


def test_the_pooled_donors_are_four_years_equally_weighted_in_2023_dollars():
    fam = pd.read_parquet(hi_cdcc.DONOR_FAMILIES_PATH)
    meta = json.loads((hi_cdcc.DONOR_FAMILIES_PATH.parent / "cps_care_donors.json").read_text())
    assert sorted(fam["asec_year"].unique()) == [2022, 2023, 2024, 2025]
    assert (fam["n13"] > 0).all()
    for _, g in fam.groupby("asec_year"):
        assert g["weight"].sum() == pytest.approx(1.0)
    assert meta["families"] == len(fam) and meta["families_paying"] == int((fam["expense"] > 0).sum())
    assert all(len(y["sha256"]) == 64 for y in meta["years"].values())
    assert meta["cpi_u_annual_average"]["2023"] == 304.702


def test_expected_expense_is_the_pay_probability_times_the_conditional_expense():
    n, earn, agi = np.array([1, 2, 1]), np.array([np.inf, 40_000.0, 5_000.0]), np.array([30_000.0, 120_000.0, 250_000.0])
    caps = hi_cdcc.caps_for_year(2025)
    got = hi_cdcc.expected_capped_expense(n, earn, caps, agi=agi)
    want = hi_cdcc.pay_probability(agi) * hi_cdcc.conditional_capped_expense(n, earn, caps)
    np.testing.assert_allclose(got, want, rtol=1e-12)
    assert (hi_cdcc.conditional_capped_expense(np.array([0]), np.array([1e9]), caps) == 0).all()
