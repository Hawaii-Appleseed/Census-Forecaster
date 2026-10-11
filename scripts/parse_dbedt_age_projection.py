"""Write ``data/external/hi_population_by_age.json`` from DBEDT's resident
population projections by age (Hawaiʻi State Data Book 2023, Table 1.24,
which reprints *Population and Economic Projections for the State of Hawaii
to 2050*, DBEDT 2050 Series, April 2024, Appendix Tables A-7 to A-9).

The bundled source file is ``data/external/dbedt_databook_2023_table_1_24.xls``
(``files.hawaii.gov/dbedt/economic/databook/2023-individual/01/012423.xls``).
Five-year age groups are folded into the filer age bands used by
``calibration.age_targets`` (16-24, 25-44, 45-64, 65+); the 15-19 group
stands in for 16-19, which only affects the band's growth *rate*, the only
thing the age margin uses. Run from the repo root::

    uv run --with xlrd python scripts/parse_dbedt_age_projection.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import xlrd

HERE = Path(__file__).resolve().parents[1]
DATA = HERE / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "external"
SRC = DATA / "dbedt_databook_2023_table_1_24.xls"
OUT = DATA / "hi_population_by_age.json"

BANDS = {"16-24": (15, 24), "25-44": (25, 44), "45-64": (45, 64), "65+": (65, 200)}


def _group_bounds(label: str) -> tuple[int, int] | None:
    label = label.strip()
    if label.startswith("Under"):
        return (0, int(label.split()[1]) - 1)
    if label.endswith("& over"):
        return (int(label.split()[0]), 200)
    if " to " in label:
        a, b = label.split(" to ")
        return (int(a), int(b))
    return None


def main() -> int:
    sh = xlrd.open_workbook(SRC).sheet_by_index(0)
    # Two panels: rows 5-27 (2020, 2030) and rows 35-57 (2040, 2050); year in
    # the header row, "Total" column at offset 1 and 4.
    series: dict[int, dict[str, float]] = {}
    totals: dict[int, float] = {}
    for header_row, first, last in ((5, 10, 27), (35, 40, 57)):
        years = [int(float(str(sh.cell_value(header_row, c)).split()[0])) for c in (1, 4)]
        for y in years:
            series[y] = {b: 0.0 for b in BANDS}
        for r in range(first, last + 1):
            label = str(sh.cell_value(r, 0))
            g = _group_bounds(label)
            if g is None:
                continue
            for y, c in zip(years, (1, 4)):
                v = float(sh.cell_value(r, c))
                for band, (lo, hi) in BANDS.items():
                    if lo <= g[0] and g[1] <= hi:
                        series[y][band] += v
        for y, c in zip(years, (1, 4)):
            totals[y] = float(sh.cell_value(8 if header_row == 5 else 38, c))
    # Check: every band sum against the printed total less the under-15 groups.
    for y in series:
        under15 = totals[y] - sum(series[y].values())
        assert 0 < under15 < 0.25 * totals[y], (y, under15)
    out = {
        "vintage": "DBEDT 2050 Series (April 2024), Hawaii State Data Book 2023 Table 1.24",
        "source": "DBEDT resident population projections by age",
        "url": "https://files.hawaii.gov/dbedt/economic/databook/2023-individual/01/012423.xls",
        "retrieved": "2026-10-10",
        "notes": [
            "State resident population as of July 1, including military personnel stationed in Hawaii.",
            "2020 total is the Census Bureau Vintage 2022 estimate; age groups are DBEDT estimates.",
            "Band 16-24 uses the 15-19 and 20-24 groups (15-year-olds included); only the band's growth rate enters the filer age margin.",
            "Years between the published points are interpolated linearly by calibration.age_targets.population_by_band.",
        ],
        "bands": list(BANDS),
        "total_population": {str(y): round(totals[y]) for y in sorted(totals)},
        "series": {str(y): {b: round(v) for b, v in series[y].items()} for y in sorted(series)},
    }
    OUT.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    for y in sorted(series):
        tot = sum(series[y].values())
        print(y, {b: f"{v:,.0f} ({v / tot:.1%})" for b, v in series[y].items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
