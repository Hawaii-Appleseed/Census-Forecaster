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
    # the primary filer's wages are AGI over the model's 0.87 factor, so a band's average wage is checkable
    df["primary_wagp"] = np.where(df["federal_agi"] > 0, df["federal_agi"] / 0.87, 0.0)
    df["secondary_wagp"] = 0.0
    if replicates:
        for i in range(1, 81):
            df[f"weight_r{i:02d}"] = df["weight"] * rng.uniform(0.7, 1.3, n)
    return df


def test_identical_populations_have_no_gap():
    u = _units()
    t = agreement.compare_populations(u, u.copy())
    assert np.allclose(t["gap"].dropna(), 0)
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
    shared = set(t.loc[t["tail_band"] & (t["group"] == "shared"), "measure"])
    assert shared == {
        "Returns, AGI $500K to $750K", "Returns, AGI $750K to $1.0M", "Returns, AGI $1.0M and up",
        "AGI $M, AGI $500K to $750K", "AGI $M, AGI $750K to $1.0M", "AGI $M, AGI $1.0M and up",
    }
    irs_tail = set(t.loc[t["tail_band"] & (t["group"] == "irs"), "measure"])
    assert "Returns, IRS band $500K to $1.0M" in irs_tail and "Wages $M, IRS band $1.0M and up" in irs_tail
    assert not any("$200K to $500K" in m for m in irs_tail)
    assert f"the {len(shared)} measures" in agreement.summarize(t)


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


def test_replicates_follow_a_weight_edit_so_the_error_measures_the_sample_not_the_edit():
    before = _units()
    after = before.copy()
    after.loc[after.index[:50], "weight"] *= 1.5
    carried = agreement.carry_weight_changes(before, after)
    reps = agreement.replicate_columns(carried)
    ratio = carried[reps].to_numpy() / before[reps].to_numpy()
    expect = (after["weight"] / before["weight"]).to_numpy()
    assert np.allclose(ratio, expect[:, None])
    naive = agreement.profile(after, with_error=True).set_index("measure").loc["Tax units", "se"]
    fixed = agreement.profile(carried, with_error=True).set_index("measure").loc["Tax units", "se"]
    assert fixed < naive          # the uncorrected replicates carry the edit as spurious error


def test_carrying_weight_changes_needs_the_same_units():
    u = _units()
    with pytest.raises(ValueError, match="same units"):
        agreement.carry_weight_changes(u, u.iloc[:-1])


def test_irs_rows_carry_the_irs_figure_and_the_gap_to_it():
    t = agreement.compare_populations(_units(), _units(seed=2)).set_index("measure")
    row = t.loc["Returns, IRS band $75K to $100K"]
    assert row["group"] == "irs" and row["irs"] == 74_340
    assert row["base_vs_irs_pct"] == pytest.approx(100 * (row["base"] / 74_340 - 1))
    assert row["poverty_vs_irs_pct"] == pytest.approx(100 * (row["poverty"] / 74_340 - 1))
    assert (t.loc[t["group"] == "shared", "irs"].isna()).all()


def test_the_average_wage_is_wages_over_wage_returns_with_its_own_standard_error():
    u = _units()
    t = agreement.profile(u, with_error=True).set_index("measure")
    lab = "$50K to $75K"
    n, wages, avg = (t.loc[f"{k}, IRS band {lab}"] for k in ("Wage returns", "Wages $M", "Average wage"))
    assert avg["value"] == pytest.approx(wages["value"] * 1e6 / n["value"])
    assert avg["se"] > 0
    inside = (u["federal_agi"] >= 50_000) & (u["federal_agi"] < 75_000)
    expect = np.average(u.loc[inside, "federal_agi"], weights=u.loc[inside, "weight"])
    assert avg["value"] == pytest.approx(expect)


def test_an_empty_class_gives_a_missing_average_not_a_crash():
    u = _units()
    u["federal_agi"] = 5_000.0                       # everyone in the $1 to $10K class
    u["primary_wagp"] = 5_000.0 / 0.87
    t = agreement.profile(u, with_error=True).set_index("measure")
    assert np.isnan(t.loc["Average wage, IRS band $200K to $500K", "value"])
    assert t.loc["Average wage, IRS band $1 to $10K", "value"] > 0


def test_frames_without_wages_are_still_compared_on_everything_else():
    u = _units().drop(columns=["primary_wagp", "secondary_wagp"])
    t = agreement.compare_populations(u, u.copy())
    assert (t["group"] == "shared").all()
