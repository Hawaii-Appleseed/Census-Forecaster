"""Tests for the FEMA disaster control (network-free).

Not a predictor — a robustness control, the analogue of exclude_2020.
The tests that matter here are the two guards, because without them the
control deletes most of the panel instead of testing it.
"""

from __future__ import annotations

import pytest

from lab.markets.screen import (
    _drop_months,
    month_index,
    months_to_indices,
)
from census_forecaster.scripts import refresh_fema_disasters as fema


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _decl(num, begin, end, itype="Flood", area="Maui (County)"):
    return {"disasterNumber": num, "declarationDate": begin + "T00:00:00.000Z",
            "incidentType": itype, "declarationTitle": f"EVENT {num}",
            "incidentBeginDate": begin + "T00:00:00.000Z",
            "incidentEndDate": (end + "T00:00:00.000Z") if end else None,
            "designatedArea": area}


# ---------------------------------------------------------------------------
# Month spans
# ---------------------------------------------------------------------------

def test_span_is_inclusive_across_a_year_boundary():
    assert fema._span_months("2014-11-04", "2015-02-25") == [
        "2014-11", "2014-12", "2015-01", "2015-02"]


def test_single_month_span():
    assert fema._span_months("2023-08-08", "2023-08-30") == ["2023-08"]


def test_open_ended_incident_counts_only_its_begin_month():
    """An unclosed declaration is an administrative state, not evidence
    that the shock is still running."""
    assert fema._span_months("2026-03-10", "") == ["2026-03"]


def test_end_before_begin_degrades_to_begin_month():
    assert fema._span_months("2023-08-08", "2022-01-01") == ["2023-08"]


# ---------------------------------------------------------------------------
# The two guards — without these the control is demolition, not a check
# ---------------------------------------------------------------------------

def test_covid_declaration_is_excluded_by_default():
    """Hawaii's COVID declaration (DR-4510) spans 2020-01 to 2023-05 —
    41 months. Dropping them all would delete three and a half years of
    panel, and the 2020 gate already handles that regime."""
    covid = {"incident_type": "Biological",
             "months": fema._span_months("2020-01-20", "2023-05-11")}
    assert len(covid["months"]) == 41
    assert fema.disaster_months([covid]) == []
    # ...and it is a default, not a hardcoded rule
    assert len(fema.disaster_months([covid], excluded_types=(),
                                    max_span_months=999)) == 41


def test_long_incident_windows_are_capped():
    long = {"incident_type": "Volcanic Eruption",
            "months": fema._span_months("2014-09-04", "2015-03-25")}
    assert len(long["months"]) == 7
    assert len(fema.disaster_months([long])) == fema.MAX_SPAN_MONTHS
    assert fema.disaster_months([long])[0] == "2014-09"   # keeps the onset


def test_months_are_deduped_and_sorted():
    a = {"incident_type": "Flood", "months": ["2023-08", "2023-09"]}
    b = {"incident_type": "Fire", "months": ["2023-08"]}
    assert fema.disaster_months([a, b]) == ["2023-08", "2023-09"]


# ---------------------------------------------------------------------------
# Fetch + dedupe
# ---------------------------------------------------------------------------

def test_one_row_per_designated_area_collapses_to_one_disaster(monkeypatch):
    """FEMA returns a row per county; a statewide event appears many
    times and must not be counted as many events."""
    payload = {"DisasterDeclarationsSummaries": [
        _decl(4724, "2023-08-08", "2023-09-30", "Fire", "Maui (County)"),
        _decl(4724, "2023-08-08", "2023-09-30", "Fire", "Hawaii (County)"),
        _decl(4793, "2024-04-11", "2024-04-14"),
    ]}
    monkeypatch.setattr(fema.requests, "get", lambda *a, **k: _Resp(payload))
    out = fema.fetch_hi_disasters()
    assert [d["disaster_number"] for d in out] == [4724, 4793]   # sorted
    assert out[0]["designated_areas"] == 2
    assert out[0]["months"] == ["2023-08", "2023-09"]


def test_empty_response_raises_rather_than_writing_nothing(monkeypatch):
    monkeypatch.setattr(fema.requests, "get",
                        lambda *a, **k: _Resp({"DisasterDeclarationsSummaries": []}))
    with pytest.raises(ValueError, match="no Hawaii declarations"):
        fema.fetch_hi_disasters()


def test_only_major_disaster_declarations_requested(monkeypatch):
    seen = {}

    def _capture(url, params=None, timeout=None):
        seen["params"] = params or {}
        return _Resp({"DisasterDeclarationsSummaries": [
            _decl(1, "2020-01-01", "2020-01-02")]})

    monkeypatch.setattr(fema.requests, "get", _capture)
    fema.fetch_hi_disasters()
    assert "declarationType eq 'DR'" in seen["params"]["$filter"]
    assert "state eq 'HI'" in seen["params"]["$filter"]


def test_payload_records_its_own_filters():
    """The exclusions are policy; a consumer must be able to see which
    ones produced the month list rather than infer them."""
    p = fema.build_payload([
        {"incident_type": "Fire", "months": ["2023-08"]}])
    assert p["candidate_exclusion_months"] == ["2023-08"]
    assert p["excluded_types"] == list(fema.DEFAULT_EXCLUDED_TYPES)
    assert p["max_span_months"] == fema.MAX_SPAN_MONTHS
    assert any("NOT a predictor" in n for n in p["limitations"])


# ---------------------------------------------------------------------------
# Screen plumbing
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("junk", ["", "bad", None, "2023-13", "2023-00", "20"])
def test_months_to_indices_rejects_junk(junk):
    """Month 13 arithmetically yields a valid-looking index (year*12+12),
    so an unvalidated parser silently accepts it and shifts a whole year
    of exclusions by one."""
    assert months_to_indices([junk]) == set()


def test_months_to_indices_keeps_good_entries_alongside_junk():
    idx = months_to_indices(["2023-08", "", "2023-13", "2018-08"])
    assert idx == {month_index(2023, 8), month_index(2018, 8)}


def test_drop_months_removes_only_the_listed_months():
    vals = {month_index(2023, 7): 1.0, month_index(2023, 8): 2.0,
            month_index(2023, 9): 3.0}
    kept = _drop_months(vals, months_to_indices(["2023-08"]))
    assert set(kept) == {month_index(2023, 7), month_index(2023, 9)}


def test_default_screen_run_is_unchanged():
    """exclude_months is opt-in: the committed screen must not silently
    start dropping months because this control landed."""
    from lab.markets.screen import run_screen
    rep = run_screen({}, {}, pairs=())
    assert rep.excluded_months == 0
