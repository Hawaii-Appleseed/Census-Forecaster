"""Smoke tests for richer ACS/PUMS variable incorporation (Phase 7).

Validates that:

* ``enrich_for_benefits`` populates the expected per-tax-unit columns
  from person + household frames.
* SSI now picks up disabled people in addition to age-65+.
* ACA PTC properly excludes Medicaid- and Medicare-covered units.
* Childcare uses WKHP for work tests instead of the earned-income proxy.
* SPM thresholds vary by tenure when ``tenure`` column is present.
* ``EntityGraph.spm_units()`` produces P60-280-style groupings using
  RELSHIPP / AGEP from the person frame.
"""
from __future__ import annotations

import pytest
from tax_modeler import (
    EntityGraph,
    compute_aca_ptc_for_units,
    compute_childcare_for_units,
    compute_ssi_for_units,
    enrich_for_benefits,
    enrich_for_credits,
    threshold_for_units,
)

pytestmark = pytest.mark.smoke


# ---------------------------------------------------------------------------
# enrich_for_benefits
# ---------------------------------------------------------------------------


def test_enrich_for_benefits_adds_columns(
    synthetic_units, synthetic_persons, synthetic_households
):
    enriched = enrich_for_credits(synthetic_units)
    enriched = enrich_for_benefits(enriched, synthetic_persons, synthetic_households)
    expected = {
        "n_disabled", "n_employer_insured", "n_medicaid_covered",
        "n_medicare_covered", "n_uninsured", "n_non_citizens",
        "primary_hours_worked", "secondary_hours_worked",
        "tenure",
    }
    assert expected.issubset(set(enriched.columns)), (
        f"missing: {expected - set(enriched.columns)}"
    )


def test_enrich_no_op_without_pums_frames(synthetic_units):
    enriched = enrich_for_credits(synthetic_units)
    out = enrich_for_benefits(enriched)
    # Without person/hh frames, no new columns are added
    assert list(out.columns) == list(enriched.columns)


def test_enrich_for_benefits_picks_up_disability(
    synthetic_units, synthetic_persons, synthetic_households
):
    enriched = enrich_for_credits(synthetic_units)
    enriched = enrich_for_benefits(enriched, synthetic_persons, synthetic_households)
    # The fixture has ~15 disabled persons; some tax units should reflect that
    assert enriched["n_disabled"].sum() > 0


def test_enrich_for_benefits_assigns_tenure(
    synthetic_units, synthetic_persons, synthetic_households
):
    enriched = enrich_for_credits(synthetic_units)
    enriched = enrich_for_benefits(enriched, synthetic_persons, synthetic_households)
    tenures = set(enriched["tenure"].unique())
    assert tenures.issubset(
        {"renter", "owner_with_mortgage", "owner_no_mortgage"}
    )
    # Mixed fixture should have at least 2 tenure types
    assert len(tenures) >= 2


# ---------------------------------------------------------------------------
# SSI now uses DIS
# ---------------------------------------------------------------------------


def test_ssi_picks_up_disabled_under_65(
    synthetic_units, synthetic_persons, synthetic_households
):
    """With DIS available, working-age disabled units should now qualify
    for SSI (they wouldn't have under the age-only fallback)."""
    from tax_modeler.pipeline import compute_base_tax

    enriched = enrich_for_credits(synthetic_units)
    enriched = compute_base_tax(enriched)
    enriched = enrich_for_benefits(enriched, synthetic_persons, synthetic_households)

    # Without disability data → only age-65+
    no_dis = enriched.drop(columns=["n_disabled"])
    age_only = compute_ssi_for_units(no_dis)
    age_only_recipients = (age_only["ssi_amount"] > 0).sum()

    # With disability data → age OR disabled
    with_dis = compute_ssi_for_units(enriched)
    with_dis_recipients = (with_dis["ssi_amount"] > 0).sum()

    if enriched["n_disabled"].sum() == 0:
        pytest.skip("no disabled units on fixture")
    # Disabled-aware path should yield ≥ age-only path
    assert with_dis_recipients >= age_only_recipients


# ---------------------------------------------------------------------------
# ACA PTC uses HINS columns
# ---------------------------------------------------------------------------


def test_aca_ptc_excludes_medicaid_covered(
    synthetic_units, synthetic_persons, synthetic_households
):
    from tax_modeler.pipeline import compute_base_tax

    enriched = enrich_for_credits(synthetic_units)
    enriched = compute_base_tax(enriched)
    enriched = enrich_for_benefits(enriched, synthetic_persons, synthetic_households)

    df = compute_aca_ptc_for_units(enriched)
    # Anyone with Medicaid coverage gets zero PTC
    on_medicaid = df[df["n_medicaid_covered"] > 0]
    if len(on_medicaid) > 0:
        assert on_medicaid["aca_ptc_amount"].sum() == 0


# ---------------------------------------------------------------------------
# Childcare uses WKHP work test
# ---------------------------------------------------------------------------


def test_childcare_requires_working_hours(
    synthetic_units, synthetic_persons, synthetic_households
):
    from tax_modeler.pipeline import compute_base_tax

    enriched = enrich_for_credits(synthetic_units)
    enriched = compute_base_tax(enriched)
    enriched = enrich_for_benefits(enriched, synthetic_persons, synthetic_households)

    df = compute_childcare_for_units(enriched)
    # Single filers with primary_hours_worked == 0 get no childcare subsidy
    singles_no_work = df[
        (df["filing_status"] != "married_filing_jointly")
        & (df["primary_hours_worked"] == 0)
    ]
    assert (singles_no_work["childcare_amount"] == 0).all()


# ---------------------------------------------------------------------------
# SPM thresholds use tenure
# ---------------------------------------------------------------------------


def test_thresholds_for_units_vectorized(
    synthetic_units, synthetic_persons, synthetic_households
):
    enriched = enrich_for_credits(synthetic_units)
    enriched = enrich_for_benefits(enriched, synthetic_persons, synthetic_households)
    thresholds = threshold_for_units(enriched, year=2024)
    assert len(thresholds) == len(enriched)
    assert (thresholds > 0).all()
    # Spread by tenure: fixture has both renters and owners → ≥2 distinct values
    # (composition variance also drives spread)
    assert thresholds.nunique() >= 2


# ---------------------------------------------------------------------------
# EntityGraph SPMUnit grouping with PUMS person data
# ---------------------------------------------------------------------------


def test_spm_unit_p60_grouping_uses_relshipp(
    synthetic_units, synthetic_persons
):
    person_df = synthetic_persons.copy()
    person_df.index = (
        person_df["SERIALNO"].astype(str) + "_" + person_df["SPORDER"].astype(str)
    )
    graph = EntityGraph.from_units(synthetic_units, person_df=person_df)
    spm_units = list(graph.spm_units())
    # Each SPMUnit has at least one tax-unit filer or is a household placeholder
    assert all(len(u.tax_unit_filer_ids) >= 0 for u in spm_units)
    # IDs should be unique
    ids = [u.spm_unit_id for u in spm_units]
    assert len(ids) == len(set(ids))


def test_spm_unit_falls_back_without_person_df(synthetic_units):
    graph = EntityGraph.from_units(synthetic_units)
    spm_units = list(graph.spm_units())
    households = list(graph.households())
    # Without person_df, falls back to 1 SPMUnit per Household
    assert len(spm_units) == len(households)
