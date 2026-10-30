"""Smoke tests for the entity layer (Phase 1 of policy-impact extension).

Verifies that ``EntityGraph`` constructs cleanly from the synthetic PUMS
fixture and that round-trips between tax units, persons, and households
preserve identifiers.
"""
from __future__ import annotations

import pytest
from tax_modeler import EntityGraph, Household, Person, SPMUnit, TaxUnit
from tax_modeler.errors import DataValidationError

pytestmark = pytest.mark.smoke


def test_graph_constructs_from_units(synthetic_units, synthetic_persons, synthetic_households):
    graph = EntityGraph.from_units(
        synthetic_units, person_df=synthetic_persons.set_index(
            synthetic_persons["SERIALNO"].astype(str) + "_" + synthetic_persons["SPORDER"].astype(str)
        ),
        hh_df=synthetic_households,
    )
    units = list(graph.tax_units())
    assert len(units) == len(synthetic_units), "graph should expose every tax unit"
    assert all(isinstance(u, TaxUnit) for u in units)


def test_tax_unit_roundtrips_to_household(synthetic_units):
    graph = EntityGraph.from_units(synthetic_units)
    for tu in graph.tax_units():
        hh = graph.household(tu.serialno)
        assert isinstance(hh, Household)
        assert tu.filer_id in hh.tax_unit_filer_ids
        assert tu.serialno == hh.serialno


def test_household_aggregates_all_tax_units(synthetic_units):
    graph = EntityGraph.from_units(synthetic_units)
    seen_filer_ids: set[int] = set()
    for hh in graph.households():
        for fid in hh.tax_unit_filer_ids:
            assert fid not in seen_filer_ids, "filer_id should appear in exactly one household"
            seen_filer_ids.add(fid)
    assert len(seen_filer_ids) == len(synthetic_units)


def test_spm_unit_phase1_one_per_household(synthetic_units):
    """Phase 1 ships SPMUnit = Household. Verify the placeholder identity."""
    graph = EntityGraph.from_units(synthetic_units)
    spm_units = list(graph.spm_units())
    households = list(graph.households())
    assert len(spm_units) == len(households)
    for spm in spm_units:
        assert isinstance(spm, SPMUnit)
        assert spm.spm_unit_id == spm.serialno


def test_person_lookup_roundtrip(synthetic_units, synthetic_persons):
    person_df = synthetic_persons.copy()
    person_df.index = (
        person_df["SERIALNO"].astype(str) + "_" + person_df["SPORDER"].astype(str)
    )
    graph = EntityGraph.from_units(synthetic_units, person_df=person_df)

    # Pick a tax unit with at least one dependent or a primary filer
    for tu in graph.tax_units():
        people = graph.people_in_tax_unit(tu.filer_id)
        if people:
            assert all(isinstance(p, Person) for p in people)
            assert any(p.person_id == tu.primary_filer_id for p in people)
            return
    pytest.fail("expected at least one tax unit with linkable persons")


def test_missing_required_column_raises(synthetic_units):
    bad = synthetic_units.drop(columns=["filer_id"])
    with pytest.raises(DataValidationError):
        EntityGraph.from_units(bad)


def test_unknown_filer_id_raises(synthetic_units):
    graph = EntityGraph.from_units(synthetic_units)
    with pytest.raises(KeyError):
        graph.tax_unit(999_999_999)
