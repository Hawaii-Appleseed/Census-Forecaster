"""SPM thresholds: Census's published Hawaii values, tenure and area, and projected years."""
from __future__ import annotations

import pandas as pd
import pytest
from tax_modeler.credits.eitc import CREDIT_PARAM_CPI_GROWTH
from tax_modeler.errors import ConfigError
from tax_modeler.poverty.thresholds import (
    _BASE_THRESHOLD_2A2C,
    _equivalence_scale,
    hawaii_spm_threshold,
    spm_threshold_table,
    threshold_for_units,
)

_LAST = max(_BASE_THRESHOLD_2A2C)

# Census, P60-290 Table 19 ("SPM Thresholds by Metro Area: 2025"), two adults and two children:
# owners with a mortgage, owners without, renters.
CENSUS_2025 = {
    "honolulu": (50_165, 39_792, 50_808),
    "kahului": (49_483, 39_371, 50_106),
    "nonmetro": (43_224, 35_501, 43_659),
}
TENURES = ("owner_with_mortgage", "owner_no_mortgage", "renter")


@pytest.mark.parametrize("area", sorted(CENSUS_2025))
def test_2025_thresholds_match_census_for_every_hawaii_area_and_tenure(area):
    for tenure, published in zip(TENURES, CENSUS_2025[area]):
        assert hawaii_spm_threshold(2025, tenure=tenure, area=area) == pytest.approx(published, abs=1.0)


# Census's 2025 renter grid (the same table's matrix, any area): thresholds by composition over 2A2C.
@pytest.mark.parametrize("n_adults,n_children,published", [
    (1, 0, 15_543), (2, 0, 21_915), (1, 1, 23_454), (2, 1, 29_518), (3, 0, 33_536),
    (3, 1, 37_357), (4, 0, 41_017), (5, 0, 47_952),
])
def test_equivalence_scale_matches_census_grid(n_adults, n_children, published):
    assert _equivalence_scale(n_adults, n_children) == pytest.approx(published / 33_536, abs=2e-4)


def test_units_take_their_own_tenure_and_county():
    units = pd.DataFrame({"n_adults": [2] * 5, "n_children": [2] * 5,
                          "tenure": ["renter", "owner_no_mortgage", "renter", "renter", None],
                          "county": ["Honolulu", "Honolulu", "Maui", "Kauai", None]})
    got = threshold_for_units(units, year=2025).round().tolist()
    assert got == pytest.approx([50_808, 39_792, 50_106, 43_659, 50_808], abs=1.0)
    no_county = threshold_for_units(units.drop(columns="county"), year=2025)
    assert no_county.iloc[2] == pytest.approx(50_808, abs=1.0)    # Urban Honolulu's line


def test_years_past_the_table_raise_by_default():
    with pytest.raises(ConfigError, match="extrapolate=True"):
        hawaii_spm_threshold(_LAST + 3)
    with pytest.raises(ConfigError):
        spm_threshold_table(_LAST + 1)


@pytest.mark.parametrize("tenure", ["renter", "owner_with_mortgage", "owner_no_mortgage"])
@pytest.mark.parametrize("n_adults,n_children", [(1, 0), (2, 2), (1, 3)])
def test_extrapolation_follows_the_credit_parameter_cpi_index(tenure, n_adults, n_children):
    kw = dict(n_adults=n_adults, n_children=n_children, tenure=tenure)
    last = hawaii_spm_threshold(_LAST, **kw)
    projected = hawaii_spm_threshold(_LAST + 3, extrapolate=True, **kw)
    assert projected == pytest.approx(last * (1.0 + CREDIT_PARAM_CPI_GROWTH) ** 3)


def test_extrapolate_leaves_tabled_years_alone_and_never_backcasts():
    assert hawaii_spm_threshold(2024, extrapolate=True) == hawaii_spm_threshold(2024)
    with pytest.raises(ConfigError):
        hawaii_spm_threshold(min(_BASE_THRESHOLD_2A2C) - 1, extrapolate=True)


def test_threshold_for_units_passes_extrapolate_through():
    units = pd.DataFrame({"n_adults": [2, 1], "n_children": [2, 0], "tenure": ["renter", "owner_no_mortgage"]})
    with pytest.raises(ConfigError):
        threshold_for_units(units, year=_LAST + 3)
    got = threshold_for_units(units, year=_LAST + 3, extrapolate=True)
    want = threshold_for_units(units, year=_LAST) * (1.0 + CREDIT_PARAM_CPI_GROWTH) ** 3
    assert got.tolist() == pytest.approx(want.tolist())


def test_poverty_impact_scores_a_projected_year_only_on_request():
    from tax_modeler.credits.arpa_ctc import arpa_ctc_for_tax_units
    from tax_modeler.poverty.impact import compute_poverty_impact

    units = arpa_ctc_for_tax_units(pd.DataFrame([{
        "filing_status": "married_filing_jointly", "num_dependents": 2,
        "num_qualifying_children": 2, "total_cash_income": 40_000.0,
        "earned_income": 40_000.0, "income": 40_000.0, "weight": 1.0,
        "hi_tax_liability": 0.0, "federal_tax_liability": 0.0, "eitc_amount": 0.0, "ctc_total": 0.0,
        "ctc_refundable": 0.0, "hi_eitc_amount": 0.0, "tenure": "renter",
        "county": "Honolulu", "house_district": 1, "senate_district": 1,
    }]))
    year = _LAST + 3
    with pytest.raises(ConfigError):
        compute_poverty_impact(units, tax_year=year, scenarios=("no_eitc",))
    res = compute_poverty_impact(units, tax_year=year, scenarios=("no_eitc",),
                                 extrapolate_thresholds=True)
    assert res.units["spm_threshold"].iloc[0] == pytest.approx(
        hawaii_spm_threshold(year, n_adults=2, n_children=2, extrapolate=True))
