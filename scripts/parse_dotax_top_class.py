"""Parse the $1M+ class from several editions of DOTAX's Individual Income Tax Statistics.

The model anchors the $1M+ class on a multi-year window, not one edition
(``calibration/dotax_base.py``, ``TOP_CLASS_WINDOW``), because the class's tax swings
year to year: $413M, $415M, $799M, $663M, $441M in TY2019-2023. This reads Table A-8
(tax liabilities for residents by Hawaiʻi AGI class) from each edition and keeps the
$1M+ row and the all-resident totals.

Usage:
    .venv/bin/python scripts/parse_dotax_top_class.py 2019=PATH/2019indinc.pdf \\
        2020=PATH/2020indinc.pdf 2021=... 2022=... 2023=PATH/2023indinc.pdf

Editions are at files.hawaii.gov/tax/stats/stats/indinc/<year>indinc.pdf (the newest)
and .../indinc/archive/<year>indinc.pdf (earlier ones). Writes
packages/tax_modeler/src/tax_modeler/data/calibration/dotax_a8_top_class.json. Each
edition's table is checked by ``parse_dotax_indinc.parse_a8``: the classes must sum
to the printed total returns and, within rounding, total tax. Table numbers move between
editions, so the table is found by title.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_dotax_indinc import _pages, parse_a8  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT = (REPO_ROOT / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "calibration"
       / "dotax_a8_top_class.json")
URL = "https://files.hawaii.gov/tax/stats/stats/indinc/{where}{year}indinc.pdf"


def top_class(pdf_path: Path, year: int) -> dict:
    pages = _pages(pdf_path)
    if f"Tax Year {year}" not in pages[0].replace("\n", " "):
        raise SystemExit(f"{pdf_path.name} is not the TY{year} edition")
    a8 = parse_a8(pages)
    top = next(r for r in a8["classes"] if r["agi_lo"] == 1_000_000)
    return {
        "returns": top["returns"], "tax_before_M": top["tax_before_M"],
        "avg_tax_before": top["avg_tax_before"],
        "total_returns": a8["total"]["returns"], "total_tax_before_M": a8["total"]["tax_before_M"],
        "source": {"file": pdf_path.name, "pages": len(pages),
                   "url": URL.format(where="" if year >= 2023 else "archive/", year=year),
                   "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest()},
    }


def main(argv: list[str]) -> int:
    if len(argv) < 2 or any("=" not in a for a in argv[1:]):
        print(__doc__)
        return 2
    years = {}
    for arg in argv[1:]:
        year, path = arg.split("=", 1)
        years[int(year)] = top_class(Path(path), int(year))
    out = {
        "note": "Table A-8 as printed: the $1M+ class's returns and tax before credits ($M), "
                "and the all-resident totals (returns include the Loss class).",
        "years": dict(sorted(years.items())),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO_ROOT)}: TY{min(years)}-{max(years)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
