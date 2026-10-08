"""Parse DOTAX's 'Earned Income Tax Credit Report' (Act 107, SLH 2017) into a bundled JSON.

DOTAX publishes it each December for the year before:
    https://files.hawaii.gov/tax/stats/stats/act107_2017/act107_earnedincome_txcredit_<YEAR>.pdf
(TY2024: December 2025, 7 pages, text layer; no OCR needed.)

Usage:
    .venv/bin/python scripts/parse_dotax_eitc_report.py PATH/TO/act107_earnedincome_txcredit_2024.pdf 2024

Writes packages/tax_modeler/src/tax_modeler/data/calibration/dotax_eitc_<YEAR>.json.
Dollars are whole dollars, as printed. The report's four tables:

    1  claims by federal AGI range: credit carried over from last year, NEW credit
       claimed for this tax year, unused credit carried to next year, and the
       credit applied this year (new + carried over - carried forward)
    2  by tax district      3  by resident status (N-11 / N-15)      4  by filing status

The rows that matter for the Act 163 sunset are the NEW credit: carryforwards are
legacy nonrefundable credits (TY2018-2022), which the 40% rate does not touch and
Act 25 (2025) ends. Every table is checked against its printed total.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path

import pdfplumber

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "packages" / "tax_modeler" / "src" / "tax_modeler" / "data" / "calibration"
URL_FMT = "https://files.hawaii.gov/tax/stats/stats/act107_2017/act107_earnedincome_txcredit_{year}.pdf"

_N = r"\$?[\d,]+"


def _n(tok: str) -> int:
    return int(tok.replace("$", "").replace(",", ""))


def parse_table1(text: str) -> dict:
    row = re.compile(
        rf"^(less than \$15,000|\$[\d,]+ to \$[\d,]+|\$55,000 or more|Total) "
        rf"({_N}) ({_N}) ({_N}) ({_N}) ({_N}) ({_N}) ({_N}) ({_N}) ({_N})$", re.M)
    rows = []
    for m in row.finditer(text):
        v = [_n(g) for g in m.groups()[1:]]
        rows.append({
            "federal_agi_range": m.group(1),
            "carried_in": {"claims": v[0], "dollars": v[1]},
            "new": {"claims": v[2], "dollars": v[3]},
            "carried_out": {"claims": v[4], "dollars": v[5]},
            "applied": {"claims": v[6], "dollars": v[7]},
            "avg_per_claim": v[8],
        })
    ranges, total = rows[:-1], rows[-1]
    if len(ranges) != 5 or total["federal_agi_range"] != "Total":
        raise ValueError(f"Table 1: expected 5 ranges + Total, got {[r['federal_agi_range'] for r in rows]}")
    for key in ("carried_in", "new", "carried_out", "applied"):
        for f in ("claims", "dollars"):
            assert sum(r[key][f] for r in ranges) == total[key][f], f"Table 1 {key} {f} does not sum"
    # applied = new + carried in - carried out, to the dollar
    assert total["applied"]["dollars"] == (total["new"]["dollars"] + total["carried_in"]["dollars"]
                                           - total["carried_out"]["dollars"]), "Table 1 identity"
    return {"ranges": ranges, "total": total}


def _label_rows(text: str, labels: list[str]) -> dict:
    """Rows whose label and numbers may sit on adjacent lines: claims, $ amount, $ per claim.

    The PDF spells the island names with a left single quote (U+2018) where the
    ʻokina is U+02BB; the text is normalized to the right glyph before matching, so
    the labels here, and the JSON keys, carry it.
    """
    flat = re.sub("[\u2018\u2019']", "\u02bb", " ".join(text.split()))
    out = {}
    for lab in labels:
        m = re.search(rf"{re.escape(lab)} ([\d,]+) \$([\d,]+) \$([\d,]+)", flat)
        if m is None:
            raise ValueError(f"row not found: {lab!r}")
        out[lab] = {"claims": _n(m.group(1)), "dollars": _n(m.group(2)), "avg_per_claim": _n(m.group(3))}
    return out


def parse_tables_2_to_4(pages: list[str], total: dict) -> dict:
    districts = _label_rows(
        "\n".join(pages), ["O\u02bbahu (District 1)", "Maui (District 2)", "Hawai\u02bbi (District 3)", "Kaua\u02bbi (District 4)"])
    status = _label_rows("\n".join(pages), ["Forms N-11", "Forms N-15"])
    filing = _label_rows("\n".join(pages), ["Single", "Joint", "Married filing separately",
                                            "Head of household", "Qualifying surviving spouse"])
    for name, tab in (("districts", districts), ("resident status", status), ("filing status", filing)):
        assert sum(r["claims"] for r in tab.values()) == total["applied"]["claims"], f"{name} claims"
        assert sum(r["dollars"] for r in tab.values()) == total["applied"]["dollars"], f"{name} dollars"
    return {"table_2_district": districts, "table_3_resident_status": status, "table_4_filing_status": filing}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    pdf_path, year = Path(argv[1]), int(argv[2])
    with pdfplumber.open(pdf_path) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
    if f"Tax Year {year}" not in pages[0].replace("\n", " "):
        raise SystemExit(f"{pdf_path.name} is not the TY{year} report")
    t1_text = next(p for p in pages if "Claims for the Earned Income Tax Credit by Income Range" in p
                   and "Table 1" in p)
    table1 = parse_table1(t1_text)
    out = {
        "source": {
            "title": f"Earned Income Tax Credit Report, Tax Year {year} (Act 107, SLH 2017)",
            "publisher": "Hawaiʻi Department of Taxation, Office of Tax Research and Planning",
            "url": URL_FMT.format(year=year), "file": pdf_path.name,
            "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(), "pages": len(pages),
            "note": "Dollars are whole dollars as printed. 'new' is the credit claimed for the tax year; "
                    "'applied' adds carryforwards used and subtracts those carried on.",
        },
        "tax_year": year,
        "table_1_by_federal_agi": table1,
        **parse_tables_2_to_4(pages, table1["total"]),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"dotax_eitc_{year}.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
