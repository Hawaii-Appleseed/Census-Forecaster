"""KFF's allocation of CBO's federal Medicaid cuts, rebuilt for Hawaiʻi."""
from __future__ import annotations

import pytest
from tax_modeler.programs import kff_medicaid_allocation as kff


@pytest.fixture(scope="module")
def inputs():
    return kff.load_inputs()


def test_cbo_table_sums_to_kffs_gross_and_net(inputs):
    gross = -sum(r["total"] for r in inputs["cbo_by_section"])
    assert gross == pytest.approx(989_667)                       # KFF: $990 billion before interactions
    by_year = sum(-r["years"][str(y)] for r in inputs["cbo_by_section"] for y in kff.YEARS)
    assert by_year == pytest.approx(gross, abs=1)
    share = (gross - inputs["kff_result"]["us"] / 1e6) / inputs["cbo_other"]["subtitle_B_interactions_total"]
    assert share == pytest.approx(0.824, abs=0.001)               # KFF: 82% of Subtitle B's interactions


def test_hawaii_shares(inputs):
    s = kff.hawaii_shares(inputs)
    assert s.expansion == pytest.approx(0.00563, abs=2e-5)
    assert s.all_states == pytest.approx(0.00375, abs=2e-5)
    assert s.provider_tax_rest == 0.0                              # not above 3.5%
    assert 0 < s.sdp_house < s.sdp_rest                            # Hawaiʻi is "likely affected"


def test_rebuild_lands_near_kff_and_is_scaled_to_it(inputs):
    raw = kff.allocate_hawaii(inputs)
    assert raw["total"].sum() == pytest.approx(3_902, rel=0.02)    # the rebuild: within 2% of KFF
    years = [str(y) for y in kff.YEARS]
    assert (raw[years].sum(axis=1) - raw["total"]).abs().max() < 1e-6
    zero = raw[raw["section"].isin(list(kff.ZERO_FOR_HAWAII))]
    assert (zero["total"] == 0).all()
    k = kff.kff_aligned(inputs)
    assert k.by_year["estimate"].sum() == pytest.approx(3_902)
    assert k.by_group["total"].sum() == pytest.approx(3_902)
    assert k.by_year["low"].sum() == pytest.approx(3_902 * 0.75)
    assert k.by_year["high"].sum() == pytest.approx(3_902 * 1.25)
    # Back-loaded, as nationally: 2034 is several times 2027.
    y = k.by_year.set_index("year")["estimate"]
    assert y[2034] > 3 * y[2027] > 0


def test_work_requirement_is_the_largest_piece(inputs):
    g = kff.kff_aligned(inputs).by_group.set_index("group")["total"]
    assert g.idxmax() == "Work requirement"
    assert g["Interactions"] < 0
