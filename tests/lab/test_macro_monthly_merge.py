"""Regression guard: macro_monthly.json writers must MERGE, never replace.

Six scripts contribute series to one shared file (market_panel,
national_macro, eia_hawaii, dbedt_mei, hta_visitors, ui_claims,
hawaii_indicators). refresh_market_panel runs FIRST in the CI workflow,
so if its write replaces the file, every later script re-adds only its
own series — and anything whose fetch failed that run is deleted from
committed history rather than merely going stale.

That happened on 2026-08-06: transient FRED timeouts dropped
MORTGAGE30US and DGS10 from the committed bundle, which silently
removed three 2020-robust rate->home-value findings from the causal
screen. These tests pin the merge semantics so it cannot recur.
"""

from __future__ import annotations

import json

import pytest

from lab.scripts.refresh_market_panel import build_macro_payload


@pytest.fixture
def existing(tmp_path):
    """A macro_monthly.json already holding other scripts' contributions."""
    path = tmp_path / "macro_monthly.json"
    path.write_text(json.dumps({
        "version": 1,
        "fetch_date": "2026-01-01",
        "series": {
            "MORTGAGE30US": [{"year": 2026, "period": "M07", "value": 6.5}],
            "DGS10": [{"year": 2026, "period": "M07", "value": 4.2}],
            "DBEDT_ARRIVALS_STATEWIDE": [
                {"year": 2026, "period": "M06", "value": 800000.0}],
            "DOL_HI_INITIAL_CLAIMS": [
                {"year": 2026, "period": "M07", "value": 1033.33}],
        },
        "sources": {"MORTGAGE30US": "FRED", "DGS10": "FRED"},
        "limitations": ["a pre-existing note from another script"],
    }))
    return path


def test_other_scripts_series_survive(existing):
    """The core regression: a market_panel write must not delete series
    contributed by national_macro / DBEDT / UI-claims / EIA / HTA."""
    out = build_macro_payload(
        {"LNS14000000": [{"year": 2026, "period": "M06", "value": 4.1}]},
        {"LNS14000000": "BLS API"},
        existing_path=existing,
    )
    for sid in ("MORTGAGE30US", "DGS10", "DBEDT_ARRIVALS_STATEWIDE",
                "DOL_HI_INITIAL_CLAIMS"):
        assert sid in out["series"], f"{sid} was destroyed by the write"
    assert out["series"]["LNS14000000"][0]["value"] == 4.1   # new one added


def test_own_series_win_on_overlap(existing):
    """This run's fresh values replace stale ones for the same key."""
    out = build_macro_payload(
        {"MORTGAGE30US": [{"year": 2026, "period": "M08", "value": 6.9}]},
        {"MORTGAGE30US": "FRED (fresh)"},
        existing_path=existing,
    )
    assert out["series"]["MORTGAGE30US"] == [
        {"year": 2026, "period": "M08", "value": 6.9}]
    assert out["sources"]["MORTGAGE30US"] == "FRED (fresh)"


def test_sources_and_limitations_preserved(existing):
    out = build_macro_payload({"X": [{"year": 2026, "period": "M01",
                                      "value": 1.0}]}, {"X": "src"},
                              existing_path=existing)
    assert out["sources"]["DGS10"] == "FRED"        # untouched
    assert "a pre-existing note from another script" in out["limitations"]
    # ...and this script's own notes are added exactly once, not duplicated
    again = build_macro_payload({"X": []}, {"X": "src"}, existing_path=existing)
    zillow_notes = [n for n in again["limitations"] if "ZORI history" in n]
    assert len(zillow_notes) == 1


def test_no_existing_file_still_works(tmp_path):
    """First-ever run: no file to merge with is not an error."""
    out = build_macro_payload(
        {"LNS14000000": [{"year": 2026, "period": "M06", "value": 4.1}]},
        {"LNS14000000": "BLS API"},
        existing_path=tmp_path / "does_not_exist.json",
    )
    assert list(out["series"]) == ["LNS14000000"]
    assert out["limitations"]          # standard notes seeded


def test_existing_path_none_is_replace_semantics(existing):
    """Callers that explicitly pass no path get a standalone payload —
    kept so the function stays usable for one-off exports, but the CI
    write site must pass existing_path (see refresh_market_panel main)."""
    out = build_macro_payload({"X": []}, {}, existing_path=None)
    assert list(out["series"]) == ["X"]
