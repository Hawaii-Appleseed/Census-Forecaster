"""enrich_for_credits reads each unit's real per-dependent records, including the repr string the raw tax-unit
parquet cache stores them as. Until 2026-11-20 only a real list was trusted, so every unit loaded from the cache
got age-10 placeholders (every dependent a child under 17)."""
from __future__ import annotations

import pandas as pd
from tax_modeler.pipeline import enrich_for_credits

KID = {"age": 4, "relationship": 25, "citizenship": 1, "months_in_home": 12, "school_level": 0, "disabled": False}
TEEN = {**KID, "age": 15}
ADULT = {**KID, "age": 52, "relationship": 27}          # an adult dependent: not a qualifying child


def _units(details, n):
    return pd.DataFrame({
        "filing_status": ["head_of_household"] * len(details), "income": [30_000.0] * len(details),
        "primary_wagp": [30_000.0] * len(details), "primary_agep": [35] * len(details),
        "num_dependents": n, "dependents_details": details, "weight": [1.0] * len(details),
        "hh_weight": [1.0] * len(details), "filer_id": [f"u{i}" for i in range(len(details))]})


def test_a_repr_string_is_parsed_into_the_real_records():
    out = enrich_for_credits(_units([repr([KID, TEEN, ADULT])], [3]))
    ages = [d["age"] for d in out["dependents_details"].iloc[0]]
    assert ages == [4, 15, 52]                              # not [10, 10, 10]
    assert out["num_children_under_13"].iloc[0] == 1.0
    assert out["num_qualifying_children"].iloc[0] == 2       # the adult dependent is not an EITC child


def test_real_lists_work_as_before_and_strings_agree_with_lists():
    as_list = enrich_for_credits(_units([[KID, TEEN]], [2]))
    as_text = enrich_for_credits(_units([repr([KID, TEEN])], [2]))
    for col in ("num_children_under_13", "num_qualifying_children"):
        assert as_list[col].iloc[0] == as_text[col].iloc[0]
    assert as_list["dependents_details"].iloc[0] == as_text["dependents_details"].iloc[0]


def test_records_that_do_not_match_the_count_fall_back_to_placeholders_but_keep_real_ages_for_under_13():
    out = enrich_for_credits(_units([repr([KID])], [2]))
    assert [d["age"] for d in out["dependents_details"].iloc[0]] == [10, 10]
    assert out["num_children_under_13"].iloc[0] == 1.0


def test_unreadable_records_give_placeholders_and_no_under_13_count():
    out = enrich_for_credits(_units(["not a list"], [1]))
    assert [d["age"] for d in out["dependents_details"].iloc[0]] == [10]
    assert pd.isna(out["num_children_under_13"].iloc[0])


def test_childless_units_have_zero_children():
    out = enrich_for_credits(_units([repr([])], [0]))
    assert out["num_children_under_13"].iloc[0] == 0.0 and out["num_qualifying_children"].iloc[0] == 0
