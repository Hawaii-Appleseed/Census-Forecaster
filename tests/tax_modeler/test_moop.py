"""benefits.moop: MOOP per SPM unit from Hawaii SPM units in Census's ACS SPM extracts."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.benefits.moop import (
    CPI_U,
    DOLLAR_YEAR,
    compute_moop_for_units,
    cpi_index,
    load_donors,
)


def _units():
    # SPM unit "a": a couple and their 70-year-old parent filing separately; "b": a single adult; one unit outside any SPM unit
    return pd.DataFrame({
        "spm_unit_id": ["a", "a", "b", None],
        "filing_status": ["married_filing_jointly", "single", "single", "single"],
        "num_dependents": [1, 0, 0, 0],
        "primary_agep": [40, 70, 30, 50],
        "secondary_agep": [38, 0, 0, 0],
        "total_cash_income": [60_000.0, 20_000.0, 35_000.0, 10_000.0],
    })


def _persons():
    return pd.DataFrame({"spm_unit_id": ["a", "a", "a", "a", "b"], "AGEP": [40, 38, 9, 70, 30]})


def test_donors_are_real_census_records():
    d = load_donors()
    assert set(d["year"]) == {2021, 2022, 2023}
    assert len(d) > 15_000
    assert (d["moop"] == 0).any() and d["moop"].max() > 50_000      # zeros and a long tail, unlike the old fixture
    assert 4_000 < np.average(d["moop"], weights=d["weight"]) < 8_000


def test_one_amount_per_spm_unit_booked_on_its_first_tax_unit():
    out = compute_moop_for_units(_units(), _persons(), year=DOLLAR_YEAR)
    m = out["moop_amount"].to_numpy()
    assert m[1] == 0 and m[0] > 0 and m[2] > 0 and m[3] > 0
    alone = compute_moop_for_units(_units().iloc[[0]].assign(spm_unit_id=None), year=DOLLAR_YEAR)["moop_amount"].iloc[0]
    assert m[0] != pytest.approx(alone)      # the four-person unit with a member over 65 is matched as such


def test_units_with_members_over_65_get_more():
    base = pd.DataFrame({"filing_status": ["single"] * 2, "num_dependents": [0, 0], "primary_agep": [40, 75],
                         "total_cash_income": [30_000.0, 30_000.0]})
    m = compute_moop_for_units(base, year=DOLLAR_YEAR)["moop_amount"].to_numpy()
    assert m[1] > m[0]


def test_dollars_follow_cpi():
    later = compute_moop_for_units(_units(), _persons(), year=2028)["moop_amount"]
    now = compute_moop_for_units(_units(), _persons(), year=DOLLAR_YEAR)["moop_amount"]
    # income is deflated before matching and the result inflated, so the cells are the same
    assert later.to_numpy() == pytest.approx(now.to_numpy() * cpi_index(2028) / CPI_U[DOLLAR_YEAR])
    assert cpi_index(2024) == CPI_U[2024] and cpi_index(2025) > CPI_U[2024]


def test_aggregation_sums_the_unit_amount_back():
    from tax_modeler.benefits.moop import compute_moop_for_units as moop
    out = moop(_units(), _persons(), year=DOLLAR_YEAR)
    per_unit = out.groupby("spm_unit_id")["moop_amount"].sum()
    assert per_unit["a"] == pytest.approx(out["moop_amount"].iloc[0])


def test_resource_draws_average_to_the_expected_moop_and_keep_its_spread():
    from tax_modeler.benefits.moop import moop_resource_draws

    hook = moop_resource_draws(_units(), _persons(), year=DOLLAR_YEAR)
    frame = pd.DataFrame({"spm_unit_id": ["a", "b", "zz"]})
    adj = hook(frame, np.random.default_rng(0), 20_000)
    assert adj.shape == (3, 20_000)
    assert (adj[2] == 0).all()                                            # not an SPM unit of these tax units
    expected = compute_moop_for_units(_units(), _persons(), year=DOLLAR_YEAR)["moop_amount"].to_numpy()[[0, 2]]
    assert np.abs(adj[:2].mean(axis=1)).max() < 0.03 * expected.max()      # zero on average
    drawn = expected[:, None] - adj[:2]                                    # the MOOP each draw gave
    assert (drawn >= -1e-9).all() and drawn.std(axis=1).min() > 0.3 * expected.min()


def test_moop_draws_spread_units_off_a_shared_cell_mean():
    """Units sharing a cell mean sit on one side of the line together; with draws each has a probability."""
    from tax_modeler.poverty.draws import DrawSpec, poverty_draws

    df = pd.DataFrame({"spm_unit_id": ["a", "b"], "spm_resources": [30_000.0, 30_000.0]})
    threshold = np.array([29_000.0, 29_000.0])

    def hook(frame, rng, n):
        return rng.normal(0.0, 3_000.0, size=(len(frame), n))

    res = poverty_draws(DrawSpec(blocks=(), resource_draws=hook, n_draws=4_000), df, threshold,
                        {"persons": np.ones(2)})
    assert 0.2 < res.poor_prob["baseline"].mean() < 0.45                  # P(N(0, 3000) < -1000) ≈ 0.37
