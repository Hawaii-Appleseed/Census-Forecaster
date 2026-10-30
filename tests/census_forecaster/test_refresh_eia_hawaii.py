"""Tests for the EIA Hawaii monthly energy-price fetcher.

Network-free: the HTTP layer is stubbed. What's pinned here is the
parsing contract, the key-resolution order, and — most importantly —
that merging into macro_monthly.json is purely additive, since that file
is shared with the BLS/FRED refresh job.
"""

from __future__ import annotations

import json

import pytest

from lab.markets.screen import MONTHLY_TARGETS
from census_forecaster.scripts import refresh_eia_hawaii as eia


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _page(rows):
    return {"response": {"data": rows}}


def test_parses_and_sorts_rows(monkeypatch):
    rows = [
        {"period": "2026-03", "price": "42.23"},
        {"period": "2026-01", "price": "40.10"},
    ]
    monkeypatch.setattr(eia.requests, "get", lambda *a, **k: _FakeResp(_page(rows)))
    out = eia.fetch_hawaii_electricity("KEY", "ALL")
    assert out == [
        {"year": 2026, "period": "M01", "value": 40.10},
        {"year": 2026, "period": "M03", "value": 42.23},
    ]


@pytest.mark.parametrize("bad", [
    {"period": "2026-03", "price": None},      # missing value
    {"period": "2026", "price": "1.0"},        # malformed period
    {"period": "2026-13", "price": "1.0"},     # impossible month
    {"period": "2026-03", "price": "abc"},     # non-numeric
])
def test_bad_rows_are_skipped_not_raised(monkeypatch, bad):
    monkeypatch.setattr(eia.requests, "get", lambda *a, **k: _FakeResp(_page([bad])))
    assert eia.fetch_hawaii_electricity("KEY", "ALL") == []


def test_key_resolution_order(monkeypatch, tmp_path):
    monkeypatch.setenv("EIA_API_KEY", "from-env")
    assert eia.resolve_api_key() == "from-env"
    assert eia.resolve_api_key("explicit") == "explicit"   # explicit wins

    monkeypatch.delenv("EIA_API_KEY", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".eia_api_key").write_text("from-file\n")
    monkeypatch.setattr(eia.Path, "home", staticmethod(lambda: home))
    assert eia.resolve_api_key() == "from-file"


def test_no_key_anywhere_returns_none(monkeypatch, tmp_path):
    monkeypatch.delenv("EIA_API_KEY", raising=False)
    monkeypatch.setattr(eia.Path, "home", staticmethod(lambda: tmp_path))
    assert eia.resolve_api_key() is None


def test_merge_is_additive(tmp_path):
    """The BLS/FRED refresh shares this file — nothing else may change."""
    path = tmp_path / "macro_monthly.json"
    existing = {
        "version": 1,
        "series": {"LNS14000000": [{"year": 2020, "period": "M01", "value": 3.5}]},
        "sources": {"BLS": "..."},
        "limitations": ["pre-existing note"],
    }
    path.write_text(json.dumps(existing))

    new = {"EIA_HI_ELEC_ALL": [{"year": 2026, "period": "M05", "value": 46.14}]}
    merged = eia.merge_into_macro_monthly(new, path=path)

    assert merged["series"]["LNS14000000"] == existing["series"]["LNS14000000"]
    assert merged["series"]["EIA_HI_ELEC_ALL"] == new["EIA_HI_ELEC_ALL"]
    assert merged["sources"]["BLS"] == "..."          # untouched
    assert "EIA" in merged["sources"]
    assert "pre-existing note" in merged["limitations"]


def test_merge_limitation_note_not_duplicated(tmp_path):
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"series": {}, "sources": {}, "limitations": []}))
    new = {"EIA_HI_ELEC_ALL": []}
    eia.merge_into_macro_monthly(new, path=path)
    path.write_text(json.dumps(eia.merge_into_macro_monthly(new, path=path)))
    merged = eia.merge_into_macro_monthly(new, path=path)
    eia_notes = [x for x in merged["limitations"] if "EIA_HI_ELEC" in x]
    assert len(eia_notes) == 1


def test_screen_target_ids_match_what_the_fetcher_writes():
    """The screen's HI_ELECTRICITY id must be a series this script emits."""
    source_id, transform = MONTHLY_TARGETS["HI_ELECTRICITY"]
    assert source_id == f"{eia.SERIES_PREFIX}ALL"
    assert source_id.replace(eia.SERIES_PREFIX, "") in eia.SECTORS
    assert transform == "log_diff"


def test_honolulu_cpi_uses_the_genuine_hawaii_series():
    """Regression guard: CUURS49ASA0 is Los Angeles (METHODOLOGY §5)."""
    source_id, _ = MONTHLY_TARGETS["HONOLULU_CPI"]
    assert source_id == "CUURS49FSA0"
    assert source_id != "CUURS49ASA0"
