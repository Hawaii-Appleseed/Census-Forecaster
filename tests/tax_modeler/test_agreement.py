"""tax_modeler.agreement: the revenue base vs the poverty population."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler import agreement


def _units(n: int = 400, seed: int = 0, *, replicates: bool = True, eitc: bool = True) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    kids = rng.integers(0, 4, n)
    df = pd.DataFrame({
        "weight": rng.uniform(5, 40, n),
        "federal_agi": rng.lognormal(10.8, 0.9, n) - 3000,
        "filing_status": rng.choice(list(agreement.STATUSES), n),
        "num_qualifying_children": kids,
    })
    if eitc:
        df["eitc_amount"] = np.where((df["federal_agi"] < 45_000) & (kids > 0), 1500.0, 0.0)
    if replicates:
        for i in range(1, 81):
            df[f"weight_r{i:02d}"] = df["weight"] * rng.uniform(0.7, 1.3, n)
    return df


def test_identical_populations_have_no_gap():
    u = _units()
    t = agreement.compare_populations(u, u.copy())
    assert np.allclose(t["gap"], 0)
    assert (t["poverty_se"] > 0).any()


def test_a_scaled_weight_is_a_uniform_percentage_gap():
    base = _units()
    pov = base.copy()
    pov["weight"] = pov["weight"] * 1.10
    t = agreement.compare_populations(base, pov).set_index("measure")
    assert t.loc["Tax units", "gap_pct"] == pytest.approx(10.0)
    assert t.loc["Qualifying children", "gap_pct"] == pytest.approx(10.0)
    assert t.loc["Federal EITC eligibility $M", "gap_pct"] == pytest.approx(10.0)


def test_standard_error_is_the_acs_replicate_formula():
    u = _units(replicates=True)
    names, X = agreement.design(u)
    w = u["weight"].to_numpy()
    reps = u[agreement.replicate_columns(u)].to_numpy().T
    i = names.index("Tax units")
    expected = np.sqrt(4 / 80 * (((reps @ X[:, i]) - w @ X[:, i]) ** 2).sum())
    got = agreement.profile(u, with_error=True).set_index("measure").loc["Tax units", "se"]
    assert got == pytest.approx(expected)


def test_error_needs_replicates():
    with pytest.raises(ValueError, match="weight_r01"):
        agreement.profile(_units(replicates=False), with_error=True)


def test_all_classes_counts_nonnegative_agi_only():
    u = _units()
    t = agreement.profile(u, with_error=False).set_index("measure")["value"]
    expect = u.loc[u["federal_agi"] >= 0, "weight"].sum()
    assert t["Returns, all classes"] == pytest.approx(expect)
    assert t["Returns, all classes"] + t["Units, AGI below $0"] == pytest.approx(t["Tax units"])


def test_children_bands_partition_units_with_children():
    u = _units()
    t = agreement.profile(u, with_error=False).set_index("measure")["value"]
    parts = sum(t[f"Units with {k} qualifying children"] for k in ("2", "3+")) + t[
        "Units with 1 qualifying child"]
    assert parts == pytest.approx(t["Units with a qualifying child"])


def test_tail_bands_are_flagged_and_left_out_of_the_summary():
    t = agreement.compare_populations(_units(), _units(seed=1))
    flagged = set(t.loc[t["tail_band"], "measure"])
    assert flagged == {
        "Returns, AGI $500K to $750K", "Returns, AGI $750K to $1.0M", "Returns, AGI $1.0M and up",
        "AGI $M, AGI $500K to $750K", "AGI $M, AGI $750K to $1.0M", "AGI $M, AGI $1.0M and up",
    }
    assert f"the {len(flagged)} measures" in agreement.summarize(t)


def test_scoring_the_eitc_leaves_numpy_state_and_the_frame_alone(monkeypatch):
    u = _units(eitc=False)
    before = u.copy()

    def fake_score(df, tax_year=2023):
        np.random.random(5)          # a scorer that draws must not move the caller's stream
        df["eitc_amount"] = 100.0
        return df

    monkeypatch.setattr("tax_modeler.pipeline.compute_base_tax", fake_score)
    np.random.seed(7)
    state = np.random.get_state()
    out = agreement.score_eitc(u)
    after = np.random.get_state()
    assert state[0] == after[0] and (state[1] == after[1]).all() and state[2] == after[2]
    assert "eitc_amount" in out.columns
    pd.testing.assert_frame_equal(u, before)


def test_the_dotax_anchor_is_attached_to_matching_measures():
    t = agreement.compare_populations(_units(), _units(seed=2)).set_index("measure")
    assert t.loc["Returns, all classes", "dotax"] == pytest.approx(631_054)
    assert pd.isna(t.loc["Qualifying children", "dotax"])
