"""Fetch Hawaii statewide ACS 1-year poverty rate and SNAP-receipt share.

Exploratory (docs/data_source_tests/caseload-leading-indicators.md). The
bundled calibration panel is county-level and has no SNAP cell, so the annual
test needs these two statewide series. Source: the Census Bureau's own
table API behind data.census.gov (api.census.gov itself now demands a key).

    S1701_C03_001E  percent of persons below poverty (state 15)
    S2201           SNAP-receiving households / total households; the
                    count cell moved from C02 (2010-14) to C03 (2015+),
                    each checked on arithmetic (receiving + not = total).

ACS 1-year 2020 was never released on the standard basis: no row for 2020.
Writes data/caseloads/hi_state_acs_poverty_snap.json only.
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "caseloads"
BASE = "https://data.census.gov/api/access/data/table?id=ACSST1Y{y}.{t}&g=040XX00US15"
YEARS = [y for y in range(2010, 2026) if y != 2020]


def get(table: str, year: int) -> tuple[list, list]:
    url = BASE.format(y=year, t=table)
    raw = subprocess.run(["curl", "-sfL", "-m", "60", url], check=True,
                         capture_output=True, text=True).stdout
    data = json.loads(raw)["response"]["data"]
    return data[0], data[1]


def main() -> None:
    out = {}
    for y in YEARS:
        ph, pr = get("S1701", y)
        sh, sr = get("S2201", y)
        total = float(sr[sh.index("S2201_C01_001E")])
        rec_col = "S2201_C02_001E" if y <= 2014 else "S2201_C03_001E"
        not_col = "S2201_C03_001E" if y <= 2014 else "S2201_C05_001E"
        rec, notrec = float(sr[sh.index(rec_col)]), float(sr[sh.index(not_col)])
        assert abs(rec + notrec - total) < 1.5, (y, rec, notrec, total)
        out[str(y)] = {
            "poverty_pct": float(pr[ph.index("S1701_C03_001E")]),
            "poverty_moe": float(pr[ph.index("S1701_C03_001M")]),
            "snap_households": rec, "households": total,
            "snap_share_pct": round(100 * rec / total, 3),
        }
        time.sleep(1)
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "hi_state_acs_poverty_snap.json").write_text(json.dumps(
        {"source": BASE, "series": out}, indent=1))
    print(len(out), "years written")


if __name__ == "__main__":
    main()
