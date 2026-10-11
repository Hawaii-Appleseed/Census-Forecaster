"""``calibration.age_targets``: the bundled DBEDT projection, the base-year
filer age mix, the forward age-band counts, and the Phase 1 age margin."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tax_modeler.calibration import age_targets as at
from tax_modeler.calibration.forward_targets import build_targets
from tax_modeler.calibration.simultaneous_calibrator import phase1_reweight


def test_bundled_projection_is_dbedt_2050_series_and_ages():
    t = at.load_population_by_age()
    assert "DBEDT 2050" in t["vintage"]
    assert sorted(t["series"]) == [2020, 2030, 2040, 2050]
    for y, row in t["series"].items():
        assert set(row) == set(at.AGE_BAND_LABELS)
    p20, p30 = at.population_by_band(2020), at.population_by_band(2030)
    share = lambda p: p["65+"] / sum(p.values())  # noqa: E731
    assert share(p20) < share(p30)                 # Hawaii ages
    assert 0.19 < p20["65+"] / t["total_population"]["2020"] < 0.21


def test_population_interpolates_between_published_years_and_holds_flat_outside():
    p25 = at.population_by_band(2025)
    p20, p30 = at.population_by_band(2020), at.population_by_band(2030)
    for b in at.AGE_BAND_LABELS:
        assert p25[b] == pytest.approx((p20[b] + p30[b]) / 2)
    assert at.population_by_band(2010) == p20
    assert at.population_by_band(2099) == at.population_by_band(2050)


def test_age_band_assignment_and_base_shares():
    assert list(at.age_band(np.array([16, 24, 25, 44, 45, 64, 65, 97]))) == \
        ["16-24", "16-24", "25-44", "25-44", "45-64", "45-64", "65+", "65+"]
    assert list(at.age_band(np.array([15.0, np.nan]))) == ["", ""]
    df = pd.DataFrame({"primary_agep": [20, 30, 70, np.nan], "weight": [1.0, 2.0, 1.0, 5.0]})
    s = at.base_age_shares(df)
    assert s == pytest.approx({"16-24": 0.25, "25-44": 0.5, "45-64": 0.0, "65+": 0.25})


def test_build_age_targets_moves_the_mix_with_the_projection_and_sums_to_total():
    base = {"16-24": 0.10, "25-44": 0.35, "45-64": 0.35, "65+": 0.20}
    counts, src = at.build_age_targets(2031, base, 650_001, base_year=2023)
    assert sum(counts.values()) == 650_001
    assert "DBEDT" in src
    assert counts["65+"] / 650_001 > 0.20          # seniors' share rises
    same, _ = at.build_age_targets(2023, base, 1000, base_year=2023)
    assert same == {b: round(v * 1000) for b, v in base.items()}


def test_forward_targets_carry_age_targets_only_when_shares_are_given():
    none = build_targets(2027, include_agi_targets=False)
    assert none.age_targets is None
    fwd = build_targets(2027, include_agi_targets=False,
                        base_age_shares={"16-24": 0.1, "25-44": 0.4, "45-64": 0.3, "65+": 0.2})
    assert sum(fwd.age_targets.values()) == sum(fwd.filer_targets.values())
    off = build_targets(2027, include_agi_targets=False, include_age_targets=False,
                        base_age_shares={"16-24": 0.1, "25-44": 0.4, "45-64": 0.3, "65+": 0.2})
    assert off.age_targets is None


def test_phase1_rakes_to_the_age_margin_and_leaves_ageless_units_to_other_margins():
    rng = np.random.default_rng(0)
    n = 3000
    df = pd.DataFrame({
        "agi": np.exp(rng.normal(10.9, 0.9, n)),
        "filing_status": rng.choice(["single", "married_filing_jointly"], n),
        "weight": rng.uniform(100, 300, n),
        "primary_agep": rng.integers(18, 90, n).astype(float),
    })
    df.loc[:49, "primary_agep"] = np.nan      # synthetic-style units without an age
    from tax_modeler.calibration import dotax_base
    filer = dotax_base.filer_targets()
    status = {"single": 331_054, "married_filing_jointly": 300_000}
    bands = at.age_band(df["primary_agep"].to_numpy())
    total_aged = 631_054 * (bands != "").mean()
    age = {"16-24": int(0.08 * total_aged), "25-44": int(0.36 * total_aged),
           "45-64": int(0.32 * total_aged), "65+": int(0.24 * total_aged)}
    out = phase1_reweight(df, filer_targets=filer, status_targets=status, age_targets=age,
                          tolerance=0.01)
    got = {b: out.loc[bands == b, "weight"].sum() for b in age}
    for b in age:
        assert got[b] == pytest.approx(age[b], rel=0.03)
    assert out.attrs["phase1_rake"]["converged"]
    assert "_age_idx" not in out.columns
