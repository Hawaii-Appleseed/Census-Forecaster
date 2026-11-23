"""The population-weighted PUMA → county / House / Senate crosswalk and apportionment."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from tax_modeler.analysis.puma_crosswalk import (
    apportion,
    apportionment_matrix,
    normalize_puma,
    population_crosswalk,
)


def test_crosswalk_covers_every_district_and_the_2020_count():
    x = population_crosswalk()
    assert x["house_district"].nunique() == 51
    assert x["senate_district"].nunique() == 25
    assert sorted(x["PUMA"].unique()) == [100, 200, 301, 302, 303, 304, 305, 306, 307, 308]
    assert int(x["population"].sum()) == 1_455_271          # 2020 census, Hawaiʻi
    assert x.groupby("PUMA")["share_of_puma"].sum().to_numpy() == pytest.approx(np.ones(10))
    # PUMA 100 is Maui and Kauaʻi together; every other PUMA lies in one county.
    counties = x.groupby("PUMA")["county"].nunique()
    assert counties.loc[100] == 2 and (counties.drop(100) == 1).all()
    by_county = x.groupby("county")["population"].sum()
    assert by_county["Honolulu"] == 1_016_508 and by_county["Hawaii"] == 200_629


@pytest.mark.parametrize("level", ["county", "house_district", "senate_district"])
def test_apportionment_keeps_totals(level):
    a = apportionment_matrix(level)
    assert a.sum(axis=1).to_numpy() == pytest.approx(np.ones(len(a)))
    totals = pd.DataFrame({"x": np.arange(1.0, 11.0), "y": 1.0}, index=a.index)
    out = apportion(totals, level)
    assert out.sum().to_numpy() == pytest.approx(totals.sum().to_numpy())


def test_apportion_splits_puma_100_by_population():
    x = population_crosswalk()
    p100 = x[x["PUMA"] == 100].groupby("county")["population"].sum()
    out = apportion(pd.DataFrame({"v": [1000.0]}, index=[100]), "county")
    assert out.loc["Kauai", "v"] == pytest.approx(1000 * p100["Kauai"] / p100.sum())
    assert set(out.index[out["v"] > 0]) == {"Kauai", "Maui"}


def test_apportion_rejects_unknown_pumas():
    with pytest.raises(ValueError):
        apportion(pd.DataFrame({"v": [1.0]}, index=[999]), "county")


def test_normalize_puma_accepts_the_survey_formats():
    assert normalize_puma(["00301", 302, "100", "99999"]).tolist()[:3] == [301, 302, 100]
    assert pd.isna(normalize_puma(["99999"]).iloc[0])


def test_assign_geography_draws_every_district_by_population():
    from tax_modeler.analysis.puma_crosswalk import assign_geography

    rng = np.random.default_rng(0)
    pumas = [100, 200, 301, 302, 303, 304, 305, 306, 307, 308]
    df = pd.DataFrame({"SERIALNO": [f"h{i}" for i in range(40_000)],
                       "PUMA": rng.choice(pumas, 40_000).astype(str)})
    g = assign_geography(df)
    assert g["house_district"].nunique() == 51 and g["senate_district"].nunique() == 25
    assert (g["house_district"] > 0).all()
    x = population_crosswalk()
    # Within each PUMA, districts come up about as often as their share of its people.
    for puma in (200, 307):
        sub = g[g["PUMA"].astype(int) == puma]
        want = x[x["PUMA"] == puma].groupby("house_district")["share_of_puma"].sum()
        got = sub["house_district"].value_counts(normalize=True).reindex(want.index, fill_value=0)
        assert (got - want).abs().max() < 0.03
    # A household split to Kauaʻi gets a Kauaʻi district, and its members all get the same one.
    kauai = set(x.loc[x["county"] == "Kauai", "house_district"])
    assert g.loc[g["county"] == "Kauai", "house_district"].isin(kauai).all()
    two = assign_geography(pd.DataFrame({"SERIALNO": ["same", "same"], "PUMA": ["00305", "305"]}))
    assert two["house_district"].nunique() == 1


def test_load_crosswalk_is_the_population_crosswalk():
    from tax_modeler.analysis.puma_crosswalk import load_crosswalk

    assert load_crosswalk()["house_district"].nunique() == 51
