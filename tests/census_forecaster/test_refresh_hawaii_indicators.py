"""Tests for the FRED + BTS Hawaii indicator fetcher (network-free)."""

from __future__ import annotations

import json

import pytest

from census_forecaster.markets.screen import HAWAII_PREDICTORS, HYPOTHESIS_PAIRS
from census_forecaster.scripts import refresh_hawaii_indicators as hi


class _Resp:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


FRED_CSV = (
    "observation_date,HIPHCI\n"
    "2026-04-01,132.97\n"
    "2026-05-01,.\n"          # FRED's missing marker
    "2026-06-01,132.23\n"
)


def test_fred_parses_and_skips_missing(monkeypatch):
    monkeypatch.setattr(hi.requests, "get", lambda *a, **k: _Resp(text=FRED_CSV))
    rows = hi.fetch_fred_monthly("HIPHCI")
    assert rows == [
        {"year": 2026, "period": "M04", "value": 132.97},
        {"year": 2026, "period": "M06", "value": 132.23},
    ]


def test_fred_request_carries_no_custom_user_agent(monkeypatch):
    """Same rule as refresh_national_macro.fetch_fred_csv: FRED stalls any custom
    or browser-like User-Agent from a datacenter IP. This module's ``_UA`` is for
    Socrata (which rejects UA-less requests), not for FRED; this fetcher is the
    one that has always worked in CI because it sends none."""
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw)
        return _Resp(text=FRED_CSV)

    monkeypatch.setattr(hi.requests, "get", fake_get)
    hi.fetch_fred_monthly("HIPHCI")
    headers = {k.lower() for k in (seen.get("headers") or {})}
    assert "user-agent" not in headers, seen.get("headers")


def test_fred_rejects_malformed_header(monkeypatch):
    monkeypatch.setattr(hi.requests, "get", lambda *a, **k: _Resp(text="onecol\n1\n"))
    with pytest.raises(ValueError, match="unexpected CSV header"):
        hi.fetch_fred_monthly("HIPHCI")


def test_bts_splits_passengers_and_departures(monkeypatch):
    payload = [
        {"reporting_month": "2026-03-01T00:00:00.000",
         "total_passengers": "898222", "total_departures": "7985"},
        {"reporting_month": "2026-04-01T00:00:00.000",
         "total_passengers": "836125", "total_departures": "7580"},
    ]
    monkeypatch.setattr(hi.requests, "get", lambda *a, **k: _Resp(payload=payload))
    out = hi.fetch_bts_hnl()
    assert out["BTS_HNL_PASSENGERS"][-1] == {
        "year": 2026, "period": "M04", "value": 836125.0}
    assert out["BTS_HNL_DEPARTURES"][0] == {
        "year": 2026, "period": "M03", "value": 7985.0}


def test_bts_sends_browser_user_agent(monkeypatch):
    """Socrata returns an empty body to bare urllib/curl requests."""
    seen = {}

    def _capture(url, params=None, headers=None, timeout=None):
        seen["headers"] = headers or {}
        seen["params"] = params or {}
        return _Resp(payload=[])

    monkeypatch.setattr(hi.requests, "get", _capture)
    hi.fetch_bts_hnl()
    assert "Mozilla" in seen["headers"].get("User-Agent", "")
    assert seen["params"]["origin_airport_code"] == "HNL"


@pytest.mark.parametrize("bad", [
    {"reporting_month": "2026", "total_passengers": "1"},
    {"reporting_month": "2026-13-01T00:00:00.000", "total_passengers": "1"},
    {"reporting_month": "2026-04-01T00:00:00.000", "total_passengers": "abc"},
    {"reporting_month": "2026-04-01T00:00:00.000", "total_passengers": None},
])
def test_bts_bad_rows_skipped(monkeypatch, bad):
    monkeypatch.setattr(hi.requests, "get", lambda *a, **k: _Resp(payload=[bad]))
    assert hi.fetch_bts_hnl()["BTS_HNL_PASSENGERS"] == []


def test_merge_is_additive(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"series": {"KEEP": [1]}, "sources": {},
                                "limitations": []}))
    merged = hi.merge_into_macro_monthly(
        {"HIPHCI": [{"year": 2026, "period": "M06", "value": 132.23}]},
        path=path)
    assert merged["series"]["KEEP"] == [1]
    assert "HIPHCI" in merged["sources"]


def test_uses_current_permit_series_not_discontinued_one():
    """HONO115BPPRIV is the better geographic match but ENDS 2013-12.
    Verified 2026-08-06 — the statewide series is the only current one."""
    assert "HIBPPRIV" in hi.FRED_SERIES
    assert "HONO115BPPRIV" not in hi.FRED_SERIES


def test_jet_fuel_and_business_formation_wired():
    """Both ride this keyless FRED fetcher rather than
    refresh_national_macro, which is driven by ml_features.
    NATIONAL_SERIES and would inject an ungated feature channel."""
    from census_forecaster.markets.screen import NATIONAL_PREDICTORS
    assert "MJFUELUSGULF" in hi.FRED_SERIES
    assert "BABATOTALSAHI" in hi.FRED_SERIES
    assert NATIONAL_PREDICTORS["US_JETFUEL"] == "MJFUELUSGULF"
    assert HAWAII_PREDICTORS["HI_BIZ_APPS"] == "BABATOTALSAHI"
    assert ("US_JETFUEL", "HI_VISITORS") in HYPOTHESIS_PAIRS
    assert ("HI_BIZ_APPS", "HI_UNEMPLOYMENT") in HYPOTHESIS_PAIRS


def test_uses_national_jet_fuel_not_dead_hawaii_series():
    """EIA's Hawaii jet fuel ended 2013-09 and the whole refiner-survey
    jet fuel family ended 2022-03 (verified 2026-08-07). The Gulf Coast
    spot benchmark is the only live one — pinned so nobody 'upgrades'
    to the better-sounding Hawaii series and silently gets a stub."""
    assert not any(s.startswith("EMA_EPJK") for s in hi.FRED_SERIES)


def test_screen_wiring_and_circularity_exclusions():
    assert HAWAII_PREDICTORS["HI_COINCIDENT"] == "HIPHCI"
    assert HAWAII_PREDICTORS["HI_AIR_PAX"] == "BTS_HNL_PASSENGERS"
    assert HAWAII_PREDICTORS["HI_PERMIT_UNITS"] == "HIBPPRIV"
    # HIPHCI is BUILT FROM the unemployment rate — screening it against
    # HI_UNEMPLOYMENT asks whether a number predicts its own ingredient.
    assert ("HI_COINCIDENT", "HI_UNEMPLOYMENT") not in HYPOTHESIS_PAIRS
    # Accommodation payrolls are a component of the employment level the
    # unemployment rate is computed against — same defect, milder.
    assert ("HI_JOBS_ACCOM", "HI_UNEMPLOYMENT") not in HYPOTHESIS_PAIRS
    # Non-circular ones stay registered.
    assert ("HI_AIR_PAX", "HI_VISITORS") in HYPOTHESIS_PAIRS
    assert ("HI_SF_SALES", "HONOLULU_ZHVI") in HYPOTHESIS_PAIRS
