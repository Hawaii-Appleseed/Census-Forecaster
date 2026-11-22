"""refresh_bps_permits: the default end year follows what Census has published, and a year whose download fails keeps
its committed values and fails the run (it used to be dropped from the file, with exit 0)."""
from __future__ import annotations

import datetime
import json

from census_forecaster.scripts import refresh_bps_permits as bps


def test_end_year_is_last_year_once_published(monkeypatch):
    monkeypatch.setattr(bps, "_published", lambda y: y <= 2025)
    assert bps.latest_published_year(datetime.date(2026, 11, 21)) == 2025
    assert bps.latest_published_year(datetime.date(2027, 3, 1)) == 2025      # 2026's file not out yet


def test_a_failed_year_keeps_its_committed_values_and_fails_the_run(tmp_path, monkeypatch):
    out = tmp_path / "bps.json"
    out.write_text(json.dumps({"values_by_geoid_year": {"15003": {"2023": 111, "2024": 222}}}))
    monkeypatch.setattr(bps, "_panel_geoids", lambda: ["15003"])

    def fetch(year, geoids):
        if year == 2024:
            raise OSError("HTTP 503")
        return {"15003": 1000 + year}

    monkeypatch.setattr(bps, "fetch_bps_year", fetch)
    code = bps.main(["--start-year", "2023", "--end-year", "2025", "--out", str(out)])
    assert code == 1
    values = json.loads(out.read_text())["values_by_geoid_year"]["15003"]
    assert values == {"2023": 3023, "2024": 222, "2025": 3025}


def test_a_clean_run_exits_zero(tmp_path, monkeypatch):
    out = tmp_path / "bps.json"
    monkeypatch.setattr(bps, "_panel_geoids", lambda: ["15003"])
    monkeypatch.setattr(bps, "fetch_bps_year", lambda year, geoids: {"15003": year})
    assert bps.main(["--start-year", "2024", "--end-year", "2025", "--out", str(out)]) == 0
