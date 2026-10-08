"""Parse DOTAX 'Hawaiʻi Individual Income Tax Statistics' (PDF) into a bundled JSON.

The report is published once a year at
    https://files.hawaii.gov/tax/stats/stats/indinc/<YEAR>indinc.pdf
(TY2023: published October 2026, 76 pages). Tables are in the text layer, so no
OCR is needed.

Usage:
    .venv/bin/python scripts/parse_dotax_indinc.py PATH/TO/2023indinc.pdf 2023

Writes packages/tax_modeler/src/tax_modeler/data/calibration/dotax_indinc_<YEAR>.json,
read by ``tax_modeler.calibration.dotax_base``. Values are as printed: dollars in
$M unless a key says otherwise, so a reviewer can diff the JSON against the PDF.

Tables parsed (appendix numbering as in the TY2023 edition):
    A-8   resident returns and tax liability before / after credits, by Hawaiʻi
          AGI class (the source of the filer and tax targets)
    A-1   resident TAXABLE returns: Hawaiʻi AGI and taxable income by class
    4     resident returns by filing status
    21    net long-term capital gains by Hawaiʻi AGI class, residents and
          nonresidents
    17A   nonresident tax liability by worldwide AGI class (totals, $400K+,
          composites)

Every table is checked against its own printed total, so a layout change in a
later edition fails here instead of landing as a wrong constant. Table numbers
move between editions (Table 21 was 22 in TY2019-2020 and 23 in TY2018): the
parser finds tables by title, not by number.
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

URL_FMT = "https://files.hawaii.gov/tax/stats/stats/indinc/{year}indinc.pdf"

_NUM = r"-?\$?[\d,]+(?:\.\d+)?"
_PCT = r"(?:n/a|-?[\d.]+%)"


def _n(tok: str) -> float | None:
    """'$1,234' / '-$6' / '12.5%' / 'n/a' -> float (percent signs dropped)."""
    tok = tok.strip()
    if tok in {"n/a", "NOT", "*"}:
        return None
    neg = tok.startswith("-")
    val = float(tok.lstrip("-").replace("$", "").replace(",", "").replace("%", ""))
    return -val if neg else val


def _class_bounds(label: str) -> tuple[float | None, float | None]:
    """'$10,000 to under $20,000' -> (10000, 20000); 'Loss' -> (None, 0)."""
    label = re.sub(r"[\"“”″]", "to under", label)   # Table 21 writes the repeated 'to under' as a ditto
    if label.startswith("Loss"):
        return None, 0.0
    if label.startswith("Less than"):
        return 0.0, _n(re.findall(r"\$[\d,]+", label)[0])
    nums = [_n(t) for t in re.findall(r"\$[\d,]+", label)]
    if "and over" in label:
        return nums[0], None
    return nums[0], nums[1]


def _pages(pdf_path: Path) -> list[str]:
    with pdfplumber.open(pdf_path) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def _find_page(pages: list[str], *needles: str) -> str:
    """The page holding every needle (appendix tables fit on one page)."""
    for text in pages:
        if all(n in text for n in needles):
            return text
    raise ValueError(f"no page with all of {needles!r}")


def parse_a8(pages: list[str]) -> dict:
    text = _find_page(pages, "TAX LIABILITIES AND EFFECTIVE TAX RATES FOR RESIDENTS",
                      "TOTAL - ALL RESIDENT RETURNS")
    row = re.compile(
        rf"^(Loss|\$[\d,]+ to under \$[\d,]+|\$1,000,000 and over) ([\d,]+) "
        rf"({_NUM}) ({_NUM}) ({_NUM}) ({_NUM}) ({_PCT}) ({_PCT}) ({_PCT}) ({_PCT})$")
    rows, total = [], None
    for line in text.splitlines():
        m = row.match(line.strip())
        if m:
            lo, hi = _class_bounds(m.group(1))
            rows.append({
                "agi_lo": lo, "agi_hi": hi, "returns": int(_n(m.group(2))),
                "tax_before_M": _n(m.group(3)), "avg_tax_before": _n(m.group(4)),
                "tax_after_M": _n(m.group(5)), "avg_tax_after": _n(m.group(6)),
                "etr_ti_before_pct": _n(m.group(7)), "etr_ti_after_pct": _n(m.group(8)),
                "etr_agi_before_pct": _n(m.group(9)), "etr_agi_after_pct": _n(m.group(10)),
            })
        elif line.startswith("TOTAL - ALL RESIDENT RETURNS"):
            t = line.split()[-9:]   # returns, tax before, avg, tax after, avg, 4 rates
            total = {"returns": int(_n(t[0])), "tax_before_M": _n(t[1]), "tax_after_M": _n(t[3])}
    if len(rows) != 16 or total is None:
        raise ValueError(f"A-8: expected 16 classes + total, got {len(rows)} / {total}")
    assert sum(r["returns"] for r in rows) == total["returns"], "A-8 returns do not sum"
    assert abs(sum(r["tax_before_M"] for r in rows) - total["tax_before_M"]) <= 3, "A-8 tax"
    return {"classes": rows, "total": total}


def parse_a1(pages: list[str]) -> dict:
    text = _find_page(pages, "SELECTED DATA FROM RESIDENT TAX RETURNS BY HAWAI'I ADJUSTED GROSS INCOME",
                      "TOTAL RESIDENT TAXABLE")
    row = re.compile(
        rf"^(\$[\d,]+ to under \$[\d,]+|\$400,000 and over) ([\d,]+) {_PCT} "
        rf"({_NUM}) {_PCT} ({_NUM}) {_PCT} ({_NUM}) {_PCT} ({_NUM}) {_PCT}$")
    rows = []
    for line in text.splitlines():
        m = row.match(line.strip())
        if m:
            lo, hi = _class_bounds(m.group(1))
            rows.append({"agi_lo": lo, "agi_hi": hi, "returns": int(_n(m.group(2))),
                         "agi_M": _n(m.group(3)), "taxable_income_M": _n(m.group(4)),
                         "tax_before_M": _n(m.group(5)), "tax_after_M": _n(m.group(6))})
    if len(rows) != 12:
        raise ValueError(f"A-1: expected 12 taxable classes, got {len(rows)}")
    tot = re.search(rf"^TOTAL RESIDENT TAXABLE ([\d,]+) {_PCT} ({_NUM})", text, re.M)
    returns, agi = int(_n(tot.group(1))), _n(tot.group(2))
    assert sum(r["returns"] for r in rows) == returns, "A-1 returns do not sum"
    assert abs(sum(r["agi_M"] for r in rows) - agi) <= 3, "A-1 AGI does not sum"
    return {"taxable_classes": rows, "taxable_total": {"returns": returns, "agi_M": agi}}


def parse_filing_status(pages: list[str], year: int) -> dict:
    """Resident returns by filing status, the block for ``year`` (Table 4)."""
    text = _find_page(pages, "by Filing Status of Taxpayer", "Qualifying Widow(er)")
    block = text.split(f"\n{year}\n", 1)[1].split("\nTOTAL", 1)[0]
    out = {}
    for line in block.splitlines():
        m = re.match(r"^(Married Filing Jointly|Single|Married Filing Separately|Head of "
                     r"Household|Qualifying Widow\(er\)|Composite) (.+)$", line)
        if not m:
            continue
        vals = re.findall(r"[\d,]+(?:\.\d+)?%?|n/a", m.group(2))
        out[m.group(1)] = int(_n(vals[2])) if vals[2] != "n/a" else 0  # resident count
    tot = re.search(rf"\n{year}\n.*?\nTOTAL ([\d,]+) [\d.]+% ([\d,]+) [\d.]+% ([\d,]+)", text, re.S)
    resident_total = int(_n(tot.group(2)))
    resident = {k: v for k, v in out.items() if k != "Composite"}
    assert sum(resident.values()) == resident_total, "filing status does not sum to resident total"
    return {"resident": resident, "resident_total": resident_total,
            "all_total": int(_n(tot.group(1))), "nonresident_total": int(_n(tot.group(3)))}


def parse_nltcg(pages: list[str]) -> dict:
    """Table 21: net long-term gains by Hawaiʻi AGI class ($M), residents / nonresidents."""
    text = _find_page(pages, "Income Eligible for the Tax Rate on Net Long-Term Capital Gains",
                      "Composite Returns")
    row = re.compile(
        rf"^(Less than \$[\d,]+|\$[\d,]+ (?:to under|[\"“”″]) \$[\d,]+|\$400,000 and over) "
        rf"([\d,]+) ({_NUM}) ([\d,]+) ({_NUM}) {_PCT} {_PCT}$")
    rows = []
    for line in text.splitlines():
        m = row.match(line.strip())
        if m:
            lo, hi = _class_bounds(m.group(1))
            rows.append({"agi_lo": lo, "agi_hi": hi,
                         "res_returns": int(_n(m.group(2))), "res_amount_M": _n(m.group(3)),
                         "nonres_returns": int(_n(m.group(4))), "nonres_amount_M": _n(m.group(5))})
    if len(rows) != 12:
        raise ValueError(f"Table 21: expected 12 classes, got {len(rows)}")
    comp = re.search(rf"^Composite Returns n/a n/a ([\d,]+) ({_NUM})", text, re.M)
    tot = re.search(rf"^TOTAL ([\d,]+) ({_NUM}) ([\d,]+) ({_NUM})", text, re.M)
    res_total, nonres_total = _n(tot.group(2)), _n(tot.group(4))
    assert abs(sum(r["res_amount_M"] for r in rows) - res_total) <= 1.5, "Table 21 resident total"
    nonres_sum = sum(r["nonres_amount_M"] for r in rows) + _n(comp.group(2))
    assert abs(nonres_sum - nonres_total) <= 1.5, "Table 21 nonresident total"
    return {"classes": rows,
            "composite": {"returns": int(_n(comp.group(1))), "amount_M": _n(comp.group(2))},
            "total": {"res_returns": int(_n(tot.group(1))), "res_amount_M": res_total,
                      "nonres_returns": int(_n(tot.group(3))), "nonres_amount_M": nonres_total}}


def parse_nonresident_liability(pages: list[str]) -> dict:
    """Table 17A totals. The returns column is split by the extractor ('1 10,261')."""
    text = _find_page(pages, "Tax Liability of Nonresidents Before and After Tax Credits",
                      "TOTAL NONRESIDENT", "Composite Returns")

    def grab(prefix: str) -> dict:
        line = next(ln for ln in text.splitlines() if ln.startswith(prefix))
        # the extractor splits the returns digits with one space ('1 10,261'): rejoin them
        parts = line[len(prefix):].split()
        k = next(i for i, t in enumerate(parts) if t.endswith("%"))
        returns = int("".join(parts[:k]).replace(",", ""))
        rest = parts[k:]   # pct, tax before, pct, avg, tax after, pct, avg
        return {"returns": returns, "tax_before_M": _n(rest[1]), "tax_after_M": _n(rest[4])}

    return {"total": grab("TOTAL NONRESIDENT"), "agi_400k_plus": grab("$400,000 and over"),
            "composite": grab("Composite Returns")}


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    pdf_path, year = Path(argv[1]), int(argv[2])
    pages = _pages(pdf_path)
    first = pages[0].replace("\n", " ")
    if f"Tax Year {year}" not in first:
        raise SystemExit(f"{pdf_path.name} is not the TY{year} report: {first[:120]!r}")

    out = {
        "source": {
            "title": f"Hawaiʻi Individual Income Tax Statistics, Tax Year {year}",
            "publisher": "Hawaiʻi Department of Taxation, Office of Tax Research & Planning",
            "url": URL_FMT.format(year=year),
            "file": pdf_path.name,
            "sha256": hashlib.sha256(pdf_path.read_bytes()).hexdigest(),
            "pages": len(pages),
            "note": "Values as printed; dollar amounts in $M unless a key says otherwise.",
        },
        "tax_year": year,
        "table_a8_resident_liability": parse_a8(pages),
        "table_a1_resident_taxable": parse_a1(pages),
        "table_4_filing_status": parse_filing_status(pages, year),
        "table_21_nltcg": parse_nltcg(pages),
        "table_17a_nonresident_liability": parse_nonresident_liability(pages),
    }
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    dest = OUT_DIR / f"dotax_indinc_{year}.json"
    dest.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {dest.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
