"""check_data_freshness: stale sources fail the monthly refresh even when every refresh step exited 0."""
from __future__ import annotations

import datetime
import json
import shutil

import pytest
from census_forecaster.scripts import check_data_freshness as fresh

D = datetime.date


@pytest.fixture
def data(tmp_path):
    """A copy of the files the check reads, made current as of 2026-12-05 (the next monthly run)."""
    rels = {*fresh.SERIES_FILES, *(r for r, _ in fresh.REFRESH_DATES), *(r for r, _ in fresh.PER_SERIES_DATES),
            *(a.file for a in fresh.ANNUAL)}
    for rel in rels:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(fresh.DATA / rel, tmp_path / rel)
    for rel, key in fresh.REFRESH_DATES:
        _edit(tmp_path, rel, lambda d, key=key: d.__setitem__(key, "2026-12-05"))
    _edit(tmp_path, "leading_indicators/national_macro.json",
          lambda d: d.__setitem__("series_fetch_date", {k: "2026-12-05" for k in d["series_fetch_date"]}))
    _edit(tmp_path, "calibration_panel/acs_panel.json", lambda d: d.__setitem__("year_range", [2010, 2025]))
    _edit(tmp_path, "leading_indicators/bps_permits.json",
          lambda d: [v.__setitem__("2025", 1) for v in d["values_by_geoid_year"].values()])
    return tmp_path


def _edit(root, rel, fn):
    d = json.loads((root / rel).read_text())
    fn(d)
    (root / rel).write_text(json.dumps(d))


def _set_last(root, rel, name, year, month):
    def fn(d):
        s = [o for o in d["series"][name] if (o["year"], int(o["period"][1:])) <= (year, month)]
        d["series"][name] = s
    _edit(root, rel, fn)


def test_every_configured_file_and_key_is_there_in_the_committed_data():
    stale, _ = fresh.check(D(2026, 11, 21))
    assert not [s for s in stale if "cannot be read" in s or ": no '" in s]


def test_current_data_passes(data):
    stale, notes = fresh.check(D(2026, 12, 5), data)
    assert stale == []
    assert len(notes) == 7 and all("Maui" in n for n in notes)


def test_a_series_past_its_lag_fails(data):
    rel = "markets/macro_monthly.json"
    _set_last(data, rel, "LNS14000000", 2026, 8)          # 4 months at the December run: within the default limit
    assert fresh.check(D(2026, 12, 5), data)[0] == []
    _set_last(data, rel, "LNS14000000", 2026, 7)          # 5 months
    stale, _ = fresh.check(D(2026, 12, 5), data)
    assert len(stale) == 1 and "LNS14000000" in stale[0] and "5 months old (limit 4)" in stale[0]


def test_a_slow_source_gets_its_own_limit(data):
    _set_last(data, "markets/macro_monthly.json", "EIA_HI_ELEC_ALL", 2026, 6)    # 6 months: EIA's limit
    assert fresh.check(D(2026, 12, 5), data)[0] == []


def test_a_refresh_date_two_months_old_fails(data):
    _edit(data, "anchors/bls_laus.json", lambda d: d.__setitem__("last_refresh", "2026-11"))
    assert fresh.check(D(2026, 12, 5), data)[0] == []      # one missed run is allowed
    _edit(data, "anchors/bls_laus.json", lambda d: d.__setitem__("last_refresh", "2026-10"))
    stale, _ = fresh.check(D(2026, 12, 5), data)
    assert len(stale) == 1 and "bls_laus.json: last_refresh 2026-10" in stale[0]


def test_one_national_macro_series_left_behind_fails(data):
    _edit(data, "leading_indicators/national_macro.json",
          lambda d: d["series_fetch_date"].__setitem__("jolts_openings", "2026-09-14"))
    stale, _ = fresh.check(D(2026, 12, 5), data)
    assert len(stale) == 1 and "jolts_openings" in stale[0]


def test_an_annual_source_is_due_only_after_its_release(data):
    _edit(data, "calibration_panel/acs_panel.json", lambda d: d.__setitem__("year_range", [2010, 2024]))
    assert fresh.check(D(2026, 9, 30), data)[0] == []      # 2025 ACS 1-year counted from October 1
    stale, _ = fresh.check(D(2026, 10, 1), data)
    assert len(stale) == 1 and "acs_panel.json: latest year 2024, 2025 is published" in stale[0]


def test_an_exemption_runs_out(data):
    stale, _ = fresh.check(D(2027, 7, 5), data)
    assert any("DBEDT_ARRIVALS_MAUI" in s and "exemption ran out" in s for s in stale)


def test_main_exits_one_when_stale(data, monkeypatch, capsys):
    monkeypatch.setattr(fresh, "DATA", data)
    monkeypatch.setattr(fresh.check, "__defaults__", (data,))
    assert fresh.main(["--today", "2026-12-05"]) == 0
    _edit(data, "anchors/bls_laus.json", lambda d: d.__setitem__("last_refresh", "2026-01"))
    assert fresh.main(["--today", "2026-12-05"]) == 1
    assert "::error::1 bundled data source(s) are stale" in capsys.readouterr().out
